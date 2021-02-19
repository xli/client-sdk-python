# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field, asdict
from typing import Optional, TypeVar, List
from ...offchain import from_json, KycDataObject, CommandRequestObject
from ... import diem_types, utils


T = TypeVar("T", bound="Base")


@dataclass
class Base:
    id: str

    @classmethod
    def resource_name(cls) -> str:
        return utils.to_snake(cls)


@dataclass
class Account(Base):
    kyc_data: str

    def kyc_data_object(self) -> KycDataObject:
        return from_json(self.kyc_data, KycDataObject)


@dataclass
class PaymentURI(Base):
    @classmethod
    def resource_name(cls) -> str:
        return "payment_uri"

    account_id: str
    subaddress_hex: str = field(metadata={"readonly": True})
    account_identifier: str = field(metadata={"readonly": True})


@dataclass
class Transaction(Base):
    currency: str
    amount: int
    status: str = field(metadata={"readonly": True, "valid-values": ["pending", "completed", "canceled"]})
    cancel_reason: Optional[str] = field(default=None, metadata={"readonly": True})
    account_id: Optional[str] = field(default=None)
    payee: Optional[str] = field(default=None)
    subaddress_hex: Optional[str] = field(default=None, metadata={"readonly": True})
    reference_id: Optional[str] = field(default=None, metadata={"readonly": True})
    signed_transaction: Optional[str] = field(default=None, metadata={"readonly": True})
    diem_transaction_version: Optional[int] = field(default=None, metadata={"readonly": True})

    @property
    def diem_txn_hash(self) -> str:
        return utils.transaction_hash(self.diem_signed_txn()) if self.signed_transaction else ""

    def diem_signed_txn(self) -> diem_types.SignedTransaction:
        return diem_types.SignedTransaction.bcs_deserialize(bytes.fromhex(str(self.signed_transaction)))

    def subaddress(self) -> bytes:
        return bytes.fromhex(str(self.subaddress_hex))

    def balance_amount(self) -> int:
        return -self.amount if self.payee else self.amount


@dataclass
class KycSamples:
    minimum: str
    reject: str
    soft_match: str
    soft_reject: str

    def match_kyc_data(self, field: str, kyc: KycDataObject) -> bool:
        subset = asdict(from_json(getattr(self, field), KycDataObject))
        return all(getattr(kyc, k) == v for k, v in subset.items() if v)

    def match_any_kyc_data(self, fields: List[str], kyc: KycDataObject) -> bool:
        return any(self.match_kyc_data(f, kyc) for f in fields)


@dataclass
class Command(Base):
    account_id: str
    reference_id: str
    request_json: str

    def request_object(self) -> CommandRequestObject:
        return from_json(self.request_json)
