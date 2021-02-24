# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from dataclasses import dataclass, field
from diem import jsonrpc, testnet
from diem.testing.miniwallet import RestClient, AppConfig
import pytest


@dataclass
class Config:
    jsonrpc_url: str = field(default_factory=lambda: testnet.JSON_RPC_URL)

    app: AppConfig = field(default_factory=lambda: AppConfig(name="app"))
    stub: AppConfig = field(default_factory=lambda: AppConfig(name="stub"))

    def create_diem_client(self) -> jsonrpc.Client:
        return jsonrpc.Client(self.jsonrpc_url)

    def start_servers(self, client: jsonrpc.Client) -> None:
        self.app.serve(client)
        self.stub.serve(client)

    def setup_accounts(self, client: jsonrpc.Client) -> None:
        self.app.setup_account(client)
        self.stub.setup_account(client)


@dataclass
class Clients:
    app: RestClient
    stub: RestClient
    diem: jsonrpc.Client


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
def travel_rule_threshold(clients) -> int:
    return clients.diem.get_metadata().dual_attestation_limit


@pytest.fixture
def hrp() -> str:
    return testnet.HRP


@pytest.fixture
def currency() -> str:
    return testnet.TEST_CURRENCY_CODE
