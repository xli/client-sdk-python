# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import asdict
from diem import testnet
from diem.testing.miniwallet import AppConfig
from diem.testing.suites import envs
from pathlib import Path
import json, logging, click, falcon, functools, pytest, os, sys, re

logging.basicConfig(level=logging.INFO, format="%(name)s [%(asctime)s] %(levelname)s: %(message)s")
basedir: Path = Path(__file__).resolve().parent.parent


click.option = functools.partial(click.option, show_default=True)  # pyre-ignore


@click.group()
def main() -> None:
    pass


@click.command()
@click.option("--name", "-n", default="mini-wallet", help="Application name.")
@click.option("--host", "-h", default="localhost", help="Start server host.")
@click.option("--port", "-p", default=8888, help="Start server port.")
@click.option("--jsonrpc", "-j", default="http://localhost:8080/v1", help="Diem fullnode JSON-RPC URL.")
@click.option("--faucet", "-f", default="http://localhost:8000/mint", help="Testnet faucet URL.")
def start_server(name: str, host: str, port: int, jsonrpc: str, faucet: str) -> None:
    configure_testnet(jsonrpc, faucet)

    conf = AppConfig(name=name, host=host, port=port)
    print("Server Config: %s" % json.dumps(asdict(conf), indent=2))

    client = testnet.create_client()
    print("Diem chain id: %s" % client.get_metadata().chain_id)

    print("setting up Diem account for server")
    conf.setup_account(client)

    api: falcon.API = conf.create_api(client)

    def openapi(req, resp) -> None:  # pyre-ignore
        resp.content_type = "application/yaml"
        resp.body = basedir.joinpath("miniwallet/openapi.yaml").read_text()

    api.add_sink(openapi, prefix="/openapi.yaml")

    conf.serve_api(api).join()


@click.command()
@click.option("--target", "-t", default="http://localhost:8888", help="Target mini-wallet application URL.")
@click.option("--jsonrpc", "-j", default="http://localhost:8080/v1", help="Diem fullnode JSON-RPC URL.")
@click.option("--faucet", "-f", default="http://localhost:8000/mint", help="Testnet faucet URL.")
@click.option("--pytest-args", default="", help="Additional pytest arguments, split by empty space, e.g. `--pytest-args '-v -s'`.", show_default=False)
def test(target: str, jsonrpc: str, faucet: str, pytest_args: str) -> None:
    configure_testnet(jsonrpc, faucet)
    os.environ[envs.TARGET_URL] = target

    args = ["--pyargs", "diem.testing.suites"] + [arg for arg in re.compile("\s+").split(pytest_args) if arg]
    print("pytest arguments: %s" % args)
    code = pytest.main(args)
    sys.stdout.flush()
    raise SystemExit(code)


def configure_testnet(jsonrpc: str, faucet: str) -> None:
    testnet.JSON_RPC_URL = jsonrpc
    testnet.FAUCET_URL = faucet
    print("Diem JSON-RPC URL: %s" % testnet.JSON_RPC_URL)
    print("Diem Testnet Faucet URL: %s" % testnet.FAUCET_URL)


main.add_command(start_server)
main.add_command(test)
