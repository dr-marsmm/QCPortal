from __future__ import annotations

from typing import Optional, Dict, Any, List, Iterable, Type, Tuple, Union, Callable

import pandas as pd
from pydantic import BaseModel, Extra, PrivateAttr, validator

from qcportal.base_models import RestModelBase, validate_list_to_single
from qcportal.records import BaseRecord, PriorityEnum, RecordStatusEnum, record_from_datamodel
from qcportal.utils import make_list


class BaseDataset(BaseModel):
    class _DataModel(BaseModel):
        class Config:
            extra = Extra.forbid
            allow_mutation = True
            validate_assignment = True

        id: int
        collection: str
        collection_type: str
        name: str
        lname: str
        description: Optional[str]
        tagline: Optional[str]
        tags: Optional[List[str]]
        group: Optional[str]
        visibility: bool
        provenance: Optional[Dict[str, Any]]

        default_tag: str
        default_priority: PriorityEnum

        extra: Optional[Dict[str, Any]] = None

        ########################################
        # Info about entries, specs, and records
        ########################################
        entry_names: Optional[List[str]]

        # To be overridden by the derived class with more specific types
        specifications: Dict[str, Any]
        entries: Optional[List[Any]]
        record_items: Optional[List[Any]]

    client: Any
    raw_data: _DataModel  # Meant to be overridden by derived classes

    # Some dataset options
    auto_fetch_missing: bool = True  # Automatically fetch missing records from the server

    # Actual optimization records, mapped via id
    _record_cache: Optional[Dict[int, BaseRecord]] = PrivateAttr()

    # To be overridden by the derived classes
    dataset_type: Optional[str] = None
    _entry_type: Optional[Type] = None
    _specification_type: Optional[Type] = None
    _record_item_type: Optional[Type] = None
    _record_type: Optional[Type] = None

    def __init__(self, **kwargs):
        BaseModel.__init__(self, **kwargs)
        self._record_cache = {}

    @property
    def offline(self) -> bool:
        return self.client is None

    def _append_entry_names(self, entry_names: List[str]):
        if self.raw_data.entry_names is None:
            self.raw_data.entry_names = []

        self.raw_data.entry_names.extend(x for x in entry_names if x not in self.raw_data.entry_names)

    def _post_add_entries(self, entry_names):
        self._append_entry_names(entry_names)

    def _post_add_specification(self, specification_name):
        # Ignoring the function argument for now... Just get all specs
        self.fetch_specifications()

    def fetch_entries(self, entry_names: Optional[Union[str, Iterable[str]]] = None):
        body_data = DatasetFetchEntryBody(names=make_list(entry_names))

        self.raw_data.entries = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/entries/bulkFetch",
            DatasetFetchEntryBody,
            None,
            List[self._entry_type],
            body_data,
            None,
        )

        # Fill in entry names as well
        fetched_names = [x.name for x in self.raw_data.entries]
        if self.raw_data.entry_names is None:
            self.raw_data.entry_names = fetched_names
        else:
            self.raw_data.entry_names.extend(x for x in fetched_names if x not in self.raw_data.entry_names)

    def fetch_entry_names(self):
        self.raw_data.entry_names = self.client._auto_request(
            "get",
            f"v1/datasets/{self.dataset_type}/{self.id}/entry_names",
            None,
            None,
            List[str],
            None,
            None,
        )

    def fetch_specifications(self):
        self.raw_data.specifications = self.client._auto_request(
            "get",
            f"v1/datasets/{self.dataset_type}/{self.id}/specifications",
            None,
            None,
            Dict[str, self._specification_type],
            None,
            None,
        )

    def fetch_record_items(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
    ):

        body_data = DatasetFetchRecordItemsBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            include=["*", "record"],
        )

        record_info = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/record_items/bulkFetch",
            DatasetFetchRecordItemsBody,
            None,
            List[self._record_item_type],
            body_data,
            None,
        )

        if self.raw_data.record_items is None:
            self.raw_data.record_items = record_info
        else:
            # Merge in newly-downloaded records
            # what spec names and entries did we just download
            new_info = [(x.specification_name, x.entry_name) for x in record_info]

            # Remove any items that match what we just downloaded, and then extend the list with the new items
            self.raw_data.record_items = [
                x for x in self.raw_data.record_items if (x.specification_name, x.entry_name) not in new_info
            ]
            self.raw_data.record_items.extend(record_info)

    def _update_metadata(self):
        new_body = DatasetModifyMetadataBody(
            name=self.raw_data.name,
            description=self.raw_data.description,
            tagline=self.raw_data.tagline,
            tags=self.raw_data.tags,
            group=self.raw_data.group,
            visibility=self.raw_data.visibility,
            provenance=self.raw_data.provenance,
            default_tag=self.raw_data.default_tag,
            default_priority=self.raw_data.default_priority,
        )

        self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}",
            DatasetModifyMetadataBody,
            None,
            None,
            new_body,
            None,
        )

    def _lookup_record(self, entry_name: str, specification_name: str):

        if self.raw_data.record_items is None:
            return None

        for ri in self.raw_data.record_items:
            if ri.specification_name == specification_name and ri.entry_name == entry_name:
                return record_from_datamodel(self.client, ri.record)

        return None

    def get_record(self, entry_name: str, specification_name: str):

        # Fetch the records if needed
        r = self._lookup_record(entry_name, specification_name)

        if r is None:
            self.fetch_record_items(entry_name, specification_name)

        return self._lookup_record(entry_name, specification_name)

    def submit(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        tag: Optional[str] = None,
        priority: PriorityEnum = None,
    ):

        body_data = DatasetSubmitBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            tag=tag,
            priority=priority,
        )

        ret = self.client._auto_request(
            "post", f"v1/datasets/{self.dataset_type}/{self.id}/submit", DatasetSubmitBody, None, Any, body_data, None
        )

        return ret

    ###################################
    # General specification management
    ###################################
    def rename_specification(self, old_name: str, new_name: str):
        name_map = {old_name: new_name}

        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/specifications",
            Dict[str, str],
            None,
            None,
            name_map,
            None,
        )

        self.raw_data.specifications = {name_map.get(x, x): y for x, y in self.raw_data.specifications.items()}

        if self.raw_data.record_items:
            for x in self.raw_data.record_items:
                x.specification_name = name_map.get(x.specification_name, x.specification_name)

        return ret

    def delete_specification(self, name: str, delete_records: bool = False):
        body_data = DatasetDeleteStrBody(names=[name], delete_records=delete_records)

        ret = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/specifications/bulkDelete",
            DatasetDeleteStrBody,
            None,
            None,
            body_data,
            None,
        )

        # Delete locally-cached stuff
        self.raw_data.specifications.pop(name, None)

        if self.raw_data.record_items:
            self.raw_data.record_items = [x for x in self.raw_data.record_items if x.specification_name != name]

        return ret

    ###################################
    # General entry management
    ###################################
    def rename_entries(self, new_name_map: Dict[str, str]):
        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/entries",
            Dict[str, str],
            None,
            None,
            new_name_map,
            None,
        )

        # rename locally cached entries and stuff
        if self.raw_data.entry_names:
            self.raw_data.entry_names = [new_name_map.get(x, x) for x in self.raw_data.entry_names]

        if self.raw_data.entries:
            for x in self.raw_data.entries:
                x.entry_name = new_name_map.get(x.entry_name, x.entry_name)

        if self.raw_data.record_items:
            for x in self.raw_data.record_items:
                x.entry_name = new_name_map.get(x.entry_name, x.entry_name)

        return ret

    def delete_entries(self, names: Union[str, Iterable[str]], delete_records: bool = False):
        names = make_list(names)
        body_data = DatasetDeleteStrBody(names=names, delete_records=delete_records)

        ret = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/entries/bulkDelete",
            DatasetDeleteStrBody,
            None,
            None,
            body_data,
            None,
        )

        # Delete locally-cached stuff
        if self.raw_data.entry_names:
            self.raw_data.entry_names = [x for x in self.raw_data.entry_names if x not in names]

        if self.raw_data.entries:
            self.raw_data.entries = [x for x in self.raw_data.entries if x.entry_name not in names]
        if self.raw_data.record_items:
            self.raw_data.record_items = [x for x in self.raw_data.record_items if x.entry_name not in names]

        return ret

    ###########################
    # Record items modification
    ###########################

    def delete_record_items(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        delete_records: bool = False,
    ):
        body_data = DatasetDeleteRecordItemsBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            delete_records=delete_records,
        )

        ret = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/record_items/bulkDelete",
            DatasetDeleteRecordItemsBody,
            None,
            None,
            body_data,
            None,
        )

        return ret

    #####################
    # Record modification
    #####################

    def modify_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        new_tag: Optional[str] = None,
        new_priority: Optional[str] = None,
        new_comment: Optional[str] = None,
        *,
        refetch_records: bool = False,
    ):
        body_data = DatasetRecordModifyBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            tag=new_tag,
            priority=new_priority,
            comment=new_comment,
        )

        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/records",
            DatasetRecordModifyBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def reset_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        *,
        refetch_records: bool = False,
    ):
        body_data = DatasetRecordModifyBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            status=RecordStatusEnum.waiting,
        )

        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/records",
            DatasetRecordModifyBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def cancel_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        *,
        refetch_records: bool = False,
    ):
        body_data = DatasetRecordModifyBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            status=RecordStatusEnum.cancelled,
        )

        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/records",
            DatasetRecordModifyBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def uncancel_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        *,
        refetch_records: bool = False,
    ):

        body_data = DatasetRecordRevertBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            revert_status=RecordStatusEnum.cancelled,
        )

        ret = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/records/revert",
            DatasetRecordRevertBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def invalidate_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        *,
        refetch_records: bool = False,
    ):
        body_data = DatasetRecordModifyBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            status=RecordStatusEnum.invalid,
        )

        ret = self.client._auto_request(
            "patch",
            f"v1/datasets/{self.dataset_type}/{self.id}/records",
            DatasetRecordModifyBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def uninvalidate_records(
        self,
        entry_names: Optional[Union[str, Iterable[str]]] = None,
        specification_names: Optional[Union[str, Iterable[str]]] = None,
        *,
        refetch_records: bool = False,
    ):
        body_data = DatasetRecordRevertBody(
            entry_names=make_list(entry_names),
            specification_names=make_list(specification_names),
            revert_status=RecordStatusEnum.invalid,
        )

        ret = self.client._auto_request(
            "post",
            f"v1/datasets/{self.dataset_type}/{self.id}/records/revert",
            DatasetRecordRevertBody,
            None,
            None,
            body_data,
            None,
        )

        if refetch_records:
            self.fetch_record_items(entry_names, specification_names)

        return ret

    def compile_values(self, value_call: Callable, value_name: str) -> pd.DataFrame:
        def _inner_call(record):
            if record is None or record.status != RecordStatusEnum.complete:
                return None
            return value_call(record)

        data_generator = (
            (entry_name, spec_name, _inner_call(record)) for entry_name, spec_name, record in self.records
        )
        df = pd.DataFrame(data_generator, columns=("entry", "specification", value_name))

        return df.pivot(index="entry", columns="specification", values=value_name)

    #########################################
    # Various properties and getters/setters
    #########################################

    def status(self) -> Dict[str, Any]:
        return self.client._auto_request(
            "get",
            f"v1/datasets/{self.dataset_type}/{self.id}/status",
            None,
            None,
            Dict[str, Dict[RecordStatusEnum, int]],
            None,
            None,
        )

    def detailed_status(self) -> List[Tuple[str, str, RecordStatusEnum]]:
        return self.client._auto_request(
            "get",
            f"v1/datasets/{self.dataset_type}/{self.id}/detailed_status",
            None,
            None,
            List[Tuple[str, str, RecordStatusEnum]],
            None,
            None,
        )

    @property
    def id(self) -> int:
        return self.raw_data.id

    @property
    def name(self) -> str:
        return self.raw_data.name

    def set_name(self, new_name: str):
        old_name = self.raw_data.name
        self.raw_data.name = new_name
        self.raw_data.lname = new_name.lower()
        try:
            self._update_metadata()
        except:
            self.raw_data.name = old_name
            raise

    @property
    def description(self) -> str:
        return self.raw_data.description

    def set_description(self, new_description: Optional[str]):
        old_description = self.raw_data.description
        self.raw_data.description = new_description
        try:
            self._update_metadata()
        except:
            self.raw_data.old_description = old_description
            raise

    @property
    def group(self):
        return self.raw_data.group

    @property
    def tags(self):
        return self.raw_data.tags

    @property
    def tagline(self):
        return self.raw_data.tagline

    @property
    def provenance(self):
        return self.raw_data.provenance

    @property
    def default_tag(self) -> Optional[str]:
        return self.raw_data.default_tag

    @property
    def default_priority(self) -> PriorityEnum:
        return self.raw_data.default_priority

    @property
    def specifications(self):
        return self.raw_data.specifications

    @property
    def entry_names(self):
        return self.raw_data.entry_names

    @property
    def entries(self):
        if self.raw_data.entries is None:
            self.fetch_entries()

        return self.raw_data.entries

    @property
    def record_items(self):
        if self.raw_data.record_items is None:
            self.fetch_record_items()
        return self.raw_data.record_items

    @property
    def records(self):
        # Get an up-to-date list of entry names and specifications
        self.fetch_entry_names()
        self.fetch_specifications()
        for entry_name in self.entry_names:
            self.fetch_record_items(entry_names=entry_name)
            for spec_name in self.specifications.keys():
                yield entry_name, spec_name, self._lookup_record(entry_name, spec_name)


class DatasetModifyMetadataBody(RestModelBase):
    name: str
    description: Optional[str]
    tags: Optional[List[str]]
    tagline: Optional[str]
    group: Optional[str]
    visibility: bool
    provenance: Optional[Dict[str, Any]]

    default_tag: str
    default_priority: PriorityEnum


class DatasetQueryModel(RestModelBase):
    dataset_type: Optional[str] = None
    name: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None


class DatasetFetchEntryBody(RestModelBase):
    names: Optional[List[str]] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    missing_ok: bool = False


class DatasetDeleteStrBody(RestModelBase):
    names: List[str]
    delete_records: bool = False


class DatasetDeleteRecordItemsBody(RestModelBase):
    entry_names: List[str]
    specification_names: List[str]
    delete_records: bool = False


class DatasetDeleteParams(RestModelBase):
    delete_records: bool = False

    @validator("delete_records", pre=True)
    def validate_lists(cls, v):
        return validate_list_to_single(v)


class DatasetFetchRecordItemsBody(RestModelBase):
    entry_names: Optional[List[str]] = None
    specification_names: Optional[List[str]] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None


class DatasetSubmitBody(RestModelBase):
    entry_names: Optional[List[str]] = None
    specification_names: Optional[List[str]] = None
    tag: Optional[str] = None
    priority: Optional[PriorityEnum] = None


class DatasetRecordModifyBody(RestModelBase):
    entry_names: Optional[List[str]] = None
    specification_names: Optional[List[str]] = None
    status: Optional[RecordStatusEnum] = None
    priority: Optional[PriorityEnum] = None
    tag: Optional[str] = None
    comment: Optional[str] = None


class DatasetRecordRevertBody(RestModelBase):
    entry_names: Optional[List[str]] = None
    specification_names: Optional[List[str]] = None
    revert_status: RecordStatusEnum = None


class DatasetQueryRecords(RestModelBase):
    record_id: List[int]
    dataset_type: Optional[List[str]] = None


class DatasetDeleteEntryBody(RestModelBase):
    names: List[str]
    delete_records: bool = False


class DatasetDeleteSpecificationBody(RestModelBase):
    names: List[str]
    delete_records: bool = False
