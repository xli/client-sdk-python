#!/usr/bin/env python3

# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import asdict
from diem import testnet
from diem.testing.miniwallet import AppConfig
import json, logging, click

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(levelname)s - %(message)s")


@click.group()
def main() -> None:
    pass


@click.command()
@click.option("--port", "-p", default=8888, help="Mini-Wallet server port.")
@click.option("--jsonrpc", "-j", default="http://localhost:8080/v1", help="Diem fullnode JSON-RPC URL.")
@click.option("--faucet", "-f", default="http://localhost:8000/mint", help="Testnet faucet URL.")
@click.option("--name", "-n", default="mini-wallet", help="Testnet faucet URL.")
def start_server(port: int, jsonrpc: str, faucet: str, name: str) -> None:
    conf = AppConfig()
    conf.port = port
    testnet.JSON_RPC_URL = jsonrpc
    testnet.FAUCET_URL = faucet

    print("Server Config: %s" % json.dumps(asdict(conf), indent=2))
    print("Diem JSON-RPC URL: %s" % testnet.JSON_RPC_URL)
    print("Diem Faucet URL: %s" % testnet.FAUCET_URL)
    client = testnet.create_client()
    print("Diem chain id: %s" % client.get_metadata().chain_id)
    print("setting up Diem account for server")
    conf.setup_account(client)
    print("starting server %s" % name)
    conf.serve(client, name).join()


main.add_command(start_server)  # pyre-ignore
