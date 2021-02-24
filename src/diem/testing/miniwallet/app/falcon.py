# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Tuple
from .app import App
from .json_input import JsonInput
from .... import offchain
import falcon, json, traceback, logging


@dataclass
class LoggerMiddleware:
    logger: logging.Logger

    def process_request(self, req, resp):  # pyre-ignore
        self.logger.info("%s %s" % (req.method, req.relative_uri))

    def process_response(self, req, resp, *args, **kwargs):  # pyre-ignore
        self.logger.info(resp.status)


def rest_handler(fn: Any):  # pyre-ignore
    def wrapper(self, req, resp, **kwargs):  # pyre-ignore
        try:
            try:
                data = json.load(req.stream)
                self.logger.info("body: %s" % data)
            except Exception:
                data = {}
            status, body = fn(self, input=JsonInput(data), **kwargs)
            resp.status = status
            resp.body = json.dumps(body)
        except ValueError as e:
            resp.status = falcon.HTTP_400
            resp.body = json.dumps({"error": str(e), "stacktrace": traceback.format_exc()})

    return wrapper


@dataclass
class Endpoints:
    app: App
    logger: logging.Logger

    @rest_handler
    def on_post_accounts(self, input: JsonInput) -> Tuple[str, Dict[str, str]]:
        return (falcon.HTTP_201, asdict(self.app.create_account(input)))

    @rest_handler
    def on_post_payments(self, account_id: str, input: JsonInput) -> Tuple[str, Dict[str, Any]]:
        return (falcon.HTTP_202, asdict(self.app.create_account_payment(account_id, input)))

    @rest_handler
    def on_post_payment_uris(self, account_id: str, input: JsonInput) -> Tuple[str, Dict[str, Any]]:
        return (falcon.HTTP_200, asdict(self.app.create_account_payment_uri(account_id, input)))

    @rest_handler
    def on_get_balances(self, account_id: str, input: JsonInput) -> Tuple[str, Dict[str, int]]:
        return (falcon.HTTP_200, self.app.get_account_balances(account_id))

    @rest_handler
    def on_get_events(self, account_id: str, input: JsonInput) -> Tuple[str, List[Dict[str, Any]]]:
        return (falcon.HTTP_200, [asdict(e) for e in self.app.get_account_events(account_id)])

    @rest_handler
    def on_get_kyc_sample(self, input: JsonInput) -> Tuple[str, Dict[str, str]]:
        return (falcon.HTTP_200, asdict(self.app.kyc_sample))

    def on_post_offchain(self, req: falcon.Request, resp: falcon.Response) -> None:
        request_id = req.get_header(offchain.X_REQUEST_ID)
        resp.set_header(offchain.X_REQUEST_ID, request_id)
        request_sender_address = req.get_header(offchain.X_REQUEST_SENDER_ADDRESS)
        input_data = req.stream.read()
        try:
            resp_obj = self.app.offchain_api(request_sender_address, input_data)
        except offchain.Error as e:
            self.logger.info(input_data)
            self.logger.exception(e)
            resp_obj = offchain.reply_request(cid=None, err=e.obj)
            resp.status = falcon.HTTP_400
        resp.body = self.app.jws_serialize(resp_obj)


def falcon_api(app: App) -> falcon.API:
    logger = logging.getLogger(app.name)
    endpoints = Endpoints(app=app, logger=logger)
    api = falcon.API(middleware=[LoggerMiddleware(logger=logger)])
    api.add_route("/accounts", endpoints, suffix="accounts")
    for res in ["balances", "payments", "payment_uris", "events"]:
        api.add_route("/accounts/{account_id}/%s" % res, endpoints, suffix=res)
    api.add_route("/kyc_sample", endpoints, suffix="kyc_sample")
    api.add_route("/v2/command", endpoints, suffix="offchain")
    app.start_sync(endpoints.logger)
    return api
