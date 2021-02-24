# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from diem import testnet
from ..miniwallet import RestClient, AppConfig
from .clients import Clients
from .envs import TARGET_URL
import pytest, os


@pytest.fixture(scope="package")
def stub_config() -> AppConfig:
    return AppConfig(name="stub-wallet")


@pytest.fixture(scope="package")
def clients(stub_config: AppConfig) -> Clients:
    diem_client = testnet.create_client()

    stub_config.setup_account(diem_client)
    stub_config.serve(diem_client)

    return Clients(
        target=RestClient(name="target-client", server_url=os.environ[TARGET_URL]).with_retry(),
        stub=stub_config.create_client(),
        diem=diem_client,
    )


@pytest.fixture(scope="package")
def target_client(clients: Clients) -> RestClient:
    return clients.target


@pytest.fixture
def hrp() -> str:
    return testnet.HRP


@pytest.fixture
def currency() -> str:
    return testnet.TEST_CURRENCY_CODE


@pytest.fixture
def travel_rule_threshold(clients: Clients) -> int:
    # todo: convert the limit base on currency
    return clients.diem.get_metadata().dual_attestation_limit
