# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from dataclasses import dataclass, field
from diem import jsonrpc, testnet, LocalAccount
from diem.testing.miniwallet import RestClient, AppConfig
import pytest, time


@dataclass
class Config:
    jsonrpc_url: str = field(default_factory=lambda: testnet.JSON_RPC_URL)

    app: AppConfig = field(default_factory=AppConfig)
    stub: AppConfig = field(default_factory=AppConfig)

    def create_diem_client(self) -> jsonrpc.Client:
        return jsonrpc.Client(self.jsonrpc_url)

    def start_servers(self, client: jsonrpc.Client) -> None:
        self.app.serve(client, "wallet")
        self.stub.serve(client, "stub")

    def setup_accounts(self, client: jsonrpc.Client) -> None:
        for app in [self.app, self.stub]:
            app.setup_account(client)


@dataclass
class Clients:
    app: RestClient
    stub: RestClient
    diem: jsonrpc.Client

    def wait_for(self, fn, tries=100, delay=0.01):
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
    return Config()


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
