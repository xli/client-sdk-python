# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from diem.miniwallet import Transaction, Command, AccountResource
from diem import offchain
from typing import List
import pytest, logging, pprint, os


logger: logging.Logger = logging.getLogger(__name__)


@pytest.mark.skipif(os.getenv("generate") is None, reason="code generator")
def test_payment_test_data_generator(currency, clients):
    amount = 1_000_000_000
    data_types = ["new_kyc_data", "new_soft_match_kyc_data", "new_reject_kyc_data", "new_soft_reject_kyc_data"]
    ret = []
    for sender_kyc in data_types:
        for receiver_kyc in data_types:
            sender = clients.app.create_account(currency, amount, kyc_data=getattr(clients.stub, sender_kyc)())
            stub_receiver = clients.stub.create_account(kyc_data=getattr(clients.app, receiver_kyc)())
            receive_payment = stub_receiver.create_receive_payment()
            send_payment = sender.send_payment(currency, amount, receive_payment.account_identifier)

            clients.wait_for(lambda: clients.app.reload(send_payment).status in ["completed", "canceled"])
            ret.append(("travel_rule", sender_kyc, receiver_kyc, command_states(stub_receiver)))
    pprint.pprint(ret)


@pytest.mark.parametrize(
    "payment_amount, sender_kyc, receiver_kyc, exchange_states",
    [
        (1, "new_kyc_data", "new_kyc_data", []),
        (999_999, "new_kyc_data", "new_kyc_data", []),
        ("travel_rule", "new_kyc_data", "new_kyc_data", ["S_INIT", "R_SEND", "READY"]),
        (
            "travel_rule",
            "new_kyc_data",
            "new_soft_match_kyc_data",
            ["S_INIT", "R_SEND", "S_SOFT", "R_SOFT_SEND", "READY"],
        ),
        ("travel_rule", "new_kyc_data", "new_reject_kyc_data", ["S_INIT", "R_SEND", "S_ABORT"]),
        (
            "travel_rule",
            "new_kyc_data",
            "new_soft_reject_kyc_data",
            ["S_INIT", "R_SEND", "S_SOFT", "R_SOFT_SEND", "S_ABORT"],
        ),
        (
            "travel_rule",
            "new_soft_match_kyc_data",
            "new_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_SEND", "READY"],
        ),
        (
            "travel_rule",
            "new_soft_match_kyc_data",
            "new_soft_match_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_SEND", "S_SOFT", "R_SOFT_SEND", "READY"],
        ),
        (
            "travel_rule",
            "new_soft_match_kyc_data",
            "new_reject_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_SEND", "S_ABORT"],
        ),
        (
            "travel_rule",
            "new_soft_match_kyc_data",
            "new_soft_reject_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_SEND", "S_SOFT", "R_SOFT_SEND", "S_ABORT"],
        ),
        ("travel_rule", "new_reject_kyc_data", "new_kyc_data", ["S_INIT", "R_ABORT"]),
        ("travel_rule", "new_reject_kyc_data", "new_soft_match_kyc_data", ["S_INIT", "R_ABORT"]),
        ("travel_rule", "new_reject_kyc_data", "new_reject_kyc_data", ["S_INIT", "R_ABORT"]),
        ("travel_rule", "new_reject_kyc_data", "new_soft_reject_kyc_data", ["S_INIT", "R_ABORT"]),
        ("travel_rule", "new_soft_reject_kyc_data", "new_kyc_data", ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_ABORT"]),
        (
            "travel_rule",
            "new_soft_reject_kyc_data",
            "new_soft_match_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_ABORT"],
        ),
        (
            "travel_rule",
            "new_soft_reject_kyc_data",
            "new_reject_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_ABORT"],
        ),
        (
            "travel_rule",
            "new_soft_reject_kyc_data",
            "new_soft_reject_kyc_data",
            ["S_INIT", "R_SOFT", "S_SOFT_SEND", "R_ABORT"],
        ),
    ],
)
@pytest.mark.parametrize("payment_method", ["send", "receive"])
def test_payment(
    payment_method, payment_amount, sender_kyc, receiver_kyc, exchange_states, currency, travel_rule_threshold, clients
):
    amount = travel_rule_threshold if payment_amount == "travel_rule" else payment_amount
    if payment_method == "send":
        sender_client = clients.app
        receiver_client = clients.stub
    else:
        sender_client = clients.stub
        receiver_client = clients.app

    sender = sender_client.create_account(currency, amount, kyc_data=getattr(receiver_client, sender_kyc)())
    receiver = receiver_client.create_account(kyc_data=getattr(sender_client, receiver_kyc)())
    sender_initial = sender.balance(currency)
    receiver_initial = receiver.balance(currency)

    receive_payment = receiver.create_receive_payment()
    send_payment = sender.send_payment(currency, amount, receive_payment.account_identifier)
    assert send_payment.currency == currency
    assert send_payment.amount == amount
    assert send_payment.payee == receive_payment.account_identifier
    clients.wait_for(lambda: sender.client.reload(send_payment).subaddress_hex)

    if exchange_states:
        stub_account = sender if clients.stub == sender.client else receiver
        clients.wait_for(wait_for_exchange_states_match(stub_account, exchange_states))
        assert command_states(stub_account) == exchange_states

        if "ABORT" in exchange_states[-1]:
            clients.wait_for(lambda: sender.client.reload(send_payment).status == "canceled")
            assert sender.balance(currency) == sender_initial
            assert receiver.balance(currency) == receiver_initial
            return

    clients.wait_for(lambda: sender.client.reload(send_payment).diem_transaction_version)

    t = clients.wait_for(
        lambda: receiver.get(Transaction, diem_transaction_version=send_payment.diem_transaction_version)
    )
    assert t.id is not None
    assert t.currency == currency
    assert t.amount == amount
    assert t.status == "completed"

    # balance changed
    assert sender.balance(currency) == sender_initial - amount
    assert receiver.balance(currency) == receiver_initial + amount


def wait_for_exchange_states_match(stub_account: AccountResource, expected_states: List[str]):
    def wait_condition():
        states = command_states(stub_account)
        logger.info("command status: %s" % states)
        if len(states) >= len(expected_states):
            return True
        return states != expected_states[: len(states)]

    return wait_condition


def payment_state_id(cmd: Command) -> str:
    return offchain.payment_state.MACHINE.match_state(cmd.request_object().command.payment).id


def command_states(account: AccountResource) -> List[str]:
    return [payment_state_id(cmd) for cmd in account.get_all(Command)]
