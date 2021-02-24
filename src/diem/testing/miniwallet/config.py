# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Dict, Any
from .client import RestClient
from .app import App, falcon_api
from ... import offchain, testnet, jsonrpc, LocalAccount
import waitress, threading, logging, falcon


@dataclass
class AppConfig:
    name: str = field(default="mini-wallet")
    url_scheme: str = field(default="http")
    host: str = field(default="localhost")
    port: int = field(default_factory=offchain.http_server.get_available_port)
    account_config: Dict[str, Any] = field(default_factory=lambda: LocalAccount().to_dict())

    initial_amount: int = field(default=1_000_000_000_000)
    initial_currency: str = field(default=testnet.TEST_CURRENCY_CODE)

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(self.name)

    @property
    def account(self) -> LocalAccount:
        return LocalAccount.from_dict(self.account_config)

    @property
    def server_url(self) -> str:
        return "%s://%s:%s" % (self.url_scheme, self.host, self.port)

    def create_client(self) -> RestClient:
        return RestClient(server_url=self.server_url).with_retry()

    def setup_account(self, client: jsonrpc.Client) -> None:
        acc = client.get_account(self.account.account_address)
        if not acc or self.need_funds(acc):
            self.logger.info("faucet mint %s" % self.account.account_address.to_hex())
            faucet = testnet.Faucet(client)
            faucet.mint(self.account.auth_key.hex(), self.initial_amount, self.initial_currency)
        if not acc or self.need_rotate(acc):
            self.logger.info("rotate dual attestation info for  %s" % self.account.account_address.to_hex())
            self.account.rotate_dual_attestation_info(client, self.server_url)

    def need_funds(self, account: jsonrpc.Account) -> bool:
        for balance in account.balances:
            if balance.currency == self.initial_currency and balance.amount > self.initial_amount / 2:
                return False
        return True

    def need_rotate(self, account: jsonrpc.Account) -> bool:
        if account.role.base_url != self.server_url:
            return True
        if not account.role.compliance_key:
            return True
        if bytes.fromhex(account.role.compliance_key) != self.account.compliance_public_key_bytes:
            return True
        return False

    def create_api(self, client: jsonrpc.Client) -> falcon.API:
        return falcon_api(App(self.account, client, self.name, self.logger))

    def serve(self, client: jsonrpc.Client) -> threading.Thread:
        return self.serve_api(self.create_api(client))

    def serve_api(self, api: falcon.API) -> threading.Thread:
        def serve() -> None:
            waitress.serve(api, host=self.host, port=self.port, clear_untrusted_proxy_headers=True, _quiet=True)

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return t
