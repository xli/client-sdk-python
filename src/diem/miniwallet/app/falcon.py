# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, asdict
from typing import Type, Generic
from .app import App
from .models import T, Account, PaymentURI, Transaction, Command
from .json_input import JsonInput
from ... import offchain, utils
import falcon, json, traceback, logging


logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class Resources(Generic[T]):
    app: App
    typ: Type[T]

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        resources = self.app.get_resources(self.typ)
        resp.body = json.dumps([asdict(res) for res in resources])

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        try:
            input = JsonInput(json.load(req.stream))
            res = self.app.create_resource(self.typ, input)
            resp.body = json.dumps(asdict(res))
        except ValueError:
            resp.status = falcon.HTTP_400
            resp.body = traceback.format_exc()


@dataclass
class OffChain:
    app: App

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        request_id = req.get_header(offchain.X_REQUEST_ID)
        resp.set_header(offchain.X_REQUEST_ID, request_id)
        request_sender_address = req.get_header(offchain.X_REQUEST_SENDER_ADDRESS)
        try:
            resp_obj = self.app.offchain_api(request_sender_address, req.stream.read())
        except offchain.Error as e:
            logger.exception(e)
            resp_obj = offchain.reply_request(cid=None, err=e.obj)
            resp.status = falcon.HTTP_400
        resp.body = self.app.jws_serialize(resp_obj)


@dataclass
class Sync:
    app: App

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        self.app.sync()
        resp.status = falcon.HTTP_204


@dataclass
class Kyc:
    app: App

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        resp.body = json.dumps(asdict(self.app.kyc_samples))


def falcon_api(app: App, stub: bool = False) -> falcon.API:
    api = falcon.API()
    for typ in [Account, Transaction, PaymentURI]:
        add_resource_route(api, app, typ)
    api.add_route("/v2/command", OffChain(app))
    api.add_route("/sync", Sync(app))
    api.add_route("/samples/kyc", Kyc(app))
    if stub:
        add_resource_route(api, app, Command)
    return api


def add_resource_route(api: falcon.API, app: App, typ: Type[T]) -> None:
    api.add_route("/%ss" % typ.resource_name(), Resources(app, typ))
