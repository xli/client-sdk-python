# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from dataclasses import dataclass, field, asdict
from diem import offchain, testnet, jsonrpc, LocalAccount
from diem.miniwallet import falcon_api, App, RestClient
from typing import Dict
from os import path
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import pytest, waitress, threading, logging, json, time, requests


logger: logging.Logger = logging.getLogger(__name__)
config_file: str = ".test-config.miniwallet.json"


@dataclass
class AppConfig:
    url_scheme: str = field(default="http")
    host: str = field(default="localhost")
    port: int = field(default_factory=offchain.http_server.get_available_port)
    account_config: Dict = field(default_factory=lambda: LocalAccount().to_dict())

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
        logger.info("account %s" % acc)
        if not acc or self.need_funds(acc):
            logger.info("faucet mint %s" % self.account.account_address)
            faucet = testnet.Faucet(client)
            faucet.mint(self.account.auth_key.hex(), self.initial_amount, self.initial_currency)
        if not acc or self.need_rotate(acc):
            logger.info("rotate dual attestation info for  %s" % self.account.account_address)
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

    def serve(self, api):
        def serve_forever():
            waitress.serve(api, host=self.host, port=self.port, clear_untrusted_proxy_headers=True)

        threading.Thread(target=serve_forever, daemon=True).start()


@dataclass
class Config:
    jsonrpc_url: str = field(default_factory=lambda: testnet.JSON_RPC_URL)

    app: AppConfig = field(default_factory=AppConfig)
    stub: AppConfig = field(default_factory=AppConfig)

    def create_diem_client(self) -> jsonrpc.Client:
        return jsonrpc.Client(self.jsonrpc_url)

    def start_servers(self, client: jsonrpc.Client) -> None:
        self.app.serve(falcon_api(App(self.app.account, client, "wallet")))
        self.stub.serve(falcon_api(App(self.stub.account, client, "stub"), stub=True))

    def setup_accounts(self, client: jsonrpc.Client) -> None:
        for app in [self.app, self.stub]:
            app.setup_account(client)


@dataclass
class Clients:
    app: RestClient
    stub: RestClient
    diem: jsonrpc.Client

    def wait_for(self, fn, tries=100, delay=0.1):
        for _ in range(tries):
            self.app.sync()
            self.stub.sync()
            try:
                ret = fn()
                if ret:
                    return ret
            except ValueError:
                time.sleep(delay)
        raise TimeoutError("waited %s secs after %s tries" % (tries * delay, tries))


@pytest.fixture(scope="package")
def config() -> Config:
    if path.exists(config_file):
        logger.info("reading config from %s" % config_file)
        with open(config_file, "r") as fp:
            config = offchain.from_dict(json.load(fp), Config)
        logger.info("config: %s" % config)
    else:
        logger.info("generating new config")
        config = Config()
        logger.info("write config to %s" % config_file)
        with open(config_file, "w") as fp:
            json.dump(asdict(config), fp)

    return config


@pytest.fixture(scope="package")
def clients(config: Config) -> Clients:
    diem_client = config.create_diem_client()
    config.setup_accounts(diem_client)
    config.start_servers(diem_client)

    return Clients(
        app=config.app.create_client(),
        stub=config.stub.create_client(),
        diem=diem_client,
    )


@pytest.fixture
def currency() -> str:
    return "XUS"


@pytest.fixture
def travel_rule_threshold(currency, clients) -> int:
    # todo: convert the limit base on currency
    return clients.diem.get_metadata().dual_attestation_limit


@pytest.fixture
def hrp(config: Config) -> str:
    return config.stub.account.hrp


@pytest.fixture
def stub_app_account(config: Config) -> LocalAccount:
    return config.stub.account
