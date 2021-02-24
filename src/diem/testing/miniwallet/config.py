# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Dict, Any
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from .client import RestClient
from ... import offchain, testnet, jsonrpc, LocalAccount

import waitress, threading, logging, requests, falcon


logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    url_scheme: str = field(default="http")
    host: str = field(default="localhost")
    port: int = field(default_factory=offchain.http_server.get_available_port)
    account_config: Dict[str, Any] = field(default_factory=lambda: LocalAccount().to_dict())

    initial_amount: int = field(default=1_000_000_000_000)
    initial_currency: str = field(default=testnet.TEST_CURRENCY_CODE)

    @property
    def account(self) -> LocalAccount:
        return LocalAccount.from_dict(self.account_config)

    @property
    def server_url(self) -> str:
        return "%s://%s:%s" % (self.url_scheme, self.host, self.port)

    def create_client(self) -> RestClient:
        session = requests.Session()
        session.mount(self.server_url, HTTPAdapter(max_retries=Retry(total=5, connect=5, backoff_factor=0.1)))
        return RestClient(server_url=self.server_url, session=session)

    def setup_account(self, client: jsonrpc.Client) -> None:
        acc = client.get_account(self.account.account_address)
        if not acc or self.need_funds(acc):
            logger.info("faucet mint %s" % self.account.account_address.to_hex())
            faucet = testnet.Faucet(client)
            faucet.mint(self.account.auth_key.hex(), self.initial_amount, self.initial_currency)
        if not acc or self.need_rotate(acc):
            logger.info("rotate dual attestation info for  %s" % self.account.account_address.to_hex())
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

    def serve(self, api: falcon.API) -> threading.Thread:
        t = threading.Thread(
            target=waitress.serve,
            args=[api],
            kwargs={
                "host": self.host,
                "port": self.port,
                "clear_untrusted_proxy_headers": True,
                "_quiet": True,
            },
            daemon=True,
        )
        t.start()
        return t
