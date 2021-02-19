# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field, replace, fields
from typing import List, Type, Optional, Any
from .app import T, Account, ReceivePayment, Transaction, KycSamples
from .. import utils, offchain
import json, requests, logging, random, string


logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class RestClient:
    server_url: str
    session: requests.Session = field(default_factory=requests.Session)

    def create_account(self, currency: str = "", amount: int = 0, kyc_data: str = "") -> "AccountResource":
        account = AccountResource(client=self, data=self.create(Account, kyc_data=kyc_data or self.new_kyc_data()))
        if currency and amount:
            account.deposit(currency, amount)
        return account

    def new_soft_match_kyc_data(self) -> str:
        return self.new_kyc_data(sample="soft_match")

    def new_reject_kyc_data(self) -> str:
        return self.new_kyc_data(sample="reject")

    def new_soft_reject_kyc_data(self) -> str:
        return self.new_kyc_data(sample="soft_reject")

    def new_kyc_data(self, name: Optional[str] = None, sample: str = "minimum") -> str:
        obj = offchain.from_json(getattr(self.kyc_samples(), sample), offchain.KycDataObject)
        if not name:
            name = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return offchain.to_json(replace(obj, legal_entity_name=name))

    def kyc_samples(self) -> KycSamples:
        return KycSamples(**self.send("GET", "/samples/kyc"))

    def reload(self, obj: T) -> T:
        ret = self.get(type(obj), id=obj.id)
        for f in fields(obj):
            setattr(obj, f.name, getattr(ret, f.name))
        return ret

    def get(self, typ: Type[T], **kwargs) -> T:  # pyre-ignore
        ret = self.get_all(typ, **kwargs)
        if len(ret) == 1:
            return ret[0]
        raise ValueError("expect one, but found %s by %s" % (ret, kwargs))

    def get_all(self, typ: Type[T], **kwargs) -> List[T]:  # pyre-ignore
        resp = self.send("GET", "/%ss" % utils.to_snake(typ))
        ret = [typ(**obj) for obj in list(resp)]
        for k, v in kwargs.items():
            ret = list(filter(lambda r: getattr(r, k) == v, ret))
        return ret

    def create(self, typ: Type[T], **kwargs) -> T:  # pyre-ignore
        return typ(**self.send("POST", "/%ss" % utils.to_snake(typ), json.dumps(kwargs)))

    def sync(self) -> None:
        self.send("POST", "/sync")

    def send(self, method: str, path: str, data: Optional[str] = None) -> Any:  # pyre-ignore
        url = "%s/%s" % (self.server_url.rstrip("/"), path.lstrip("/"))
        logger.info("%s %s: %s" % (method, path, data))
        resp = self.session.request(method=method, url=url.lower(), data=data)
        logger.info("response status code: %s" % resp.status_code)
        logger.debug(resp.text)
        resp.raise_for_status()
        if resp.headers.get("content-type") == "application/json":
            return resp.json()
        return resp.text


@dataclass
class AccountResource:

    client: RestClient
    data: Account

    @property
    def id(self) -> str:
        return self.data.id

    @property
    def kyc_data(self) -> str:
        return self.data.kyc_data

    def deposit(self, currency: str, amount: int) -> Transaction:
        return self.client.create(Transaction, account_id=self.id, currency=currency, amount=amount)

    def send_payment(self, currency: str, amount: int, payee: str) -> Transaction:
        return self.client.create(Transaction, account_id=self.id, payee=payee, currency=currency, amount=amount)

    def create_receive_payment(self) -> ReceivePayment:
        return self.client.create(ReceivePayment, account_id=self.id)

    def balance(self, currency: str) -> int:
        txns = self.get_all(Transaction, currency=currency)
        return sum([t.balance_amount() for t in txns if t.status != "canceled"])

    def get(self, typ: Type[T], **kwargs) -> T:  # pyre-ignore
        return self.client.get(typ, account_id=self.id, **kwargs)

    def get_all(self, typ: Type[T], **kwargs) -> List[T]:  # pyre-ignore
        return self.client.get_all(typ, account_id=self.id, **kwargs)
