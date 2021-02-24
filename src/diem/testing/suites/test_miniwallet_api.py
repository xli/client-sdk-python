# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from diem.testing.miniwallet import Account, Transaction
from diem import identifier
from .envs import INCLUDE_DEBUG_API
import pytest, requests, time, json, os


def test_create_account_resource_without_balance(target_client):
    account = target_client.create_account()
    assert account.balances() == {}


def test_create_account_with_kyc_data_and_balances(target_client, currency):
    kyc_data = target_client.new_kyc_data()
    account = target_client.create_account(kyc_data=kyc_data, balances={currency: 100})
    assert account.id
    assert account.kyc_data == kyc_data
    assert account.balances() == {currency: 100}
    assert account.balance(currency) == 100

@pytest.mark.skipif(os.getenv(INCLUDE_DEBUG_API), reason="env variable %r is not set" % INCLUDE_DEBUG_API)
def test_create_account_event(target_client, currency):
    before_timestamp = int(time.time() * 1000)
    account = target_client.create_account()
    after_timestamp = int(time.time() * 1000)

    events = account.events()
    assert len(events) == 1
    event = events[0]
    assert event.id
    assert event.timestamp >= before_timestamp
    assert event.timestamp <= after_timestamp
    assert event.type == "created_account"
    event_data = Account(**json.loads(event.data))
    assert event_data.kyc_data == account.kyc_data
    assert event_data.id == account.id


@pytest.mark.parametrize(
    "err_msg, kyc_data, balances",
    [
        ("'kyc_data' is required", None, None),
        ("'kyc_data' must be JSON-encoded KycDataObject", "invalid json", None),
        ("'kyc_data' must be JSON-encoded KycDataObject", "{}", None),
        ("'currency' is invalid", "sample", {"invalid": 11}),
        ("'currency' is invalid", "sample", {22: 11}),
        ("'amount' value must be greater than or equal to zero", "sample", {"XUS": -11}),
        ("'amount' type must be 'int'", "sample", {"XUS": "11"}),
    ],
)
def test_create_account_with_invalid_data(target_client, currency, err_msg, kyc_data, balances):
    if kyc_data == "sample":
        kyc_data = target_client.new_kyc_data()

    with pytest.raises(requests.exceptions.HTTPError, match="400 Client Error") as einfo:
        target_client.create("/accounts", kyc_data=kyc_data, balances=balances)
    assert err_msg in einfo.value.response.text


def test_create_account_payment_uri(target_client, hrp):
    account = target_client.create_account()
    ret = account.create_payment_uri()
    assert ret.account_id == account.id
    assert ret.subaddress_hex
    assert ret.intent(hrp).account_id
    address, subaddress = identifier.decode_account(ret.intent(hrp).account_id, hrp)
    assert address
    assert subaddress
    assert ret.subaddress_hex == subaddress.hex()

@pytest.mark.skipif(os.getenv(INCLUDE_DEBUG_API), reason="env variable %r is not set" % INCLUDE_DEBUG_API)
def test_create_account_payment_uri_events(target_client, hrp):
    account = target_client.create_account()
    index = len(account.events())
    ret = account.create_payment_uri()
    assert len(account.events(index)) == 1
    assert account.events(index)[0].type == "created_payment_uri"

def test_send_payment_and_events(clients, hrp, currency):
    receiver = clients.target.create_account()
    payment_uri = receiver.create_payment_uri()

    amount = 1234
    sender = clients.stub.create_account(balances={currency: amount})
    assert sender.balance(currency) == amount

    index = len(sender.events())
    payment = sender.send_payment(currency, amount, payment_uri.intent(hrp).account_id)
    assert payment.account_id == sender.id
    assert payment.currency == currency
    assert payment.amount == amount
    assert payment.payee == payment_uri.intent(hrp).account_id

    sender.wait_for_balance(currency, 0)
    receiver.wait_for_balance(currency, amount)

    sender.wait_for_event("updated_transaction", status=Transaction.Status.completed, start_index=index)
    new_events = sender.events(index)
    assert len(new_events) == 4
    assert new_events[0].type == "created_transaction"
    assert new_events[1].type == "updated_transaction"
    assert sorted(list(json.loads(new_events[1].data).keys())) == ["id", "subaddress_hex"]
    assert new_events[2].type == "updated_transaction"
    assert sorted(list(json.loads(new_events[2].data).keys())) == ["id", "signed_transaction"]
    assert new_events[3].type == "updated_transaction"
    assert sorted(list(json.loads(new_events[3].data).keys())) == ["diem_transaction_version", "id", "status"]


def test_receive_payment_and_events(clients, hrp, currency):
    receiver = clients.stub.create_account()
    payment_uri = receiver.create_payment_uri()

    index = len(receiver.events())
    amount = 1234
    sender = clients.target.create_account({currency: amount})
    payment = sender.send_payment(currency, amount, payment_uri.intent(hrp).account_id)

    receiver.wait_for_balance(currency, amount)
    sender.wait_for_balance(currency, 0)

    new_events = receiver.events(index)
    assert len(new_events) == 1
    assert new_events[0].type == "created_transaction"
    txn = Transaction(**json.loads(new_events[0].data))
    assert txn.id
    assert txn.currency == payment.currency
    assert txn.amount == payment.amount
    assert txn.diem_transaction_version


def test_receive_multiple_payments(clients, hrp, currency):
    receiver = clients.stub.create_account()
    payment_uri = receiver.create_payment_uri()

    index = len(receiver.events())
    amount = 1234
    sender1 = clients.target.create_account({currency: amount})
    sender1.send_payment(currency, amount, payment_uri.intent(hrp).account_id)

    sender2 = clients.target.create_account({currency: amount})
    sender2.send_payment(currency, amount, payment_uri.intent(hrp).account_id)

    sender1.wait_for_balance(currency, 0)
    sender2.wait_for_balance(currency, 0)
    receiver.wait_for_balance(currency, amount * 2)

    new_events = receiver.events(index)
    assert len(new_events) == 2
    assert new_events[0].type == "created_transaction"
    assert new_events[1].type == "created_transaction"


@pytest.mark.parametrize(
    "invalid_payee",
    [
        "invalid id",
        "dm1p7ujcndcl7nudzwt8fglhx6wxnvqqqqqqqqqqqqqd8p9cq",
        "tdm1p7ujcndcl7nudzwt8fglhx6wxnvqqqqqqqqqqqqqv88j4x",
    ],
)
def test_send_payment_payee_is_invalid(clients, currency, invalid_payee, hrp):
    sender = clients.stub.create_account({currency: 100})

    index = len(sender.events())
    with pytest.raises(requests.exceptions.HTTPError, match="400 Client Error") as einfo:
        sender.send_payment(currency, 1, invalid_payee)
    assert "'payee' is invalid account identifier" in einfo.value.response.text
    assert sender.balance(currency) == 100
    assert sender.events(index) == []


def test_return_client_error_if_send_payment_more_than_account_balance(clients, currency, hrp):
    receiver = clients.target.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: 100})

    index = len(sender.events())
    with pytest.raises(requests.exceptions.HTTPError, match="400 Client Error") as einfo:
        sender.send_payment(currency, 101, payment_uri.intent(hrp).account_id)
    assert "account balance not enough" in einfo.value.response.text
    assert sender.balance(currency) == 100
    assert sender.events(index) == []


def test_send_payment_meets_travel_rule_limit(clients, currency, travel_rule_threshold, hrp):
    amount = travel_rule_threshold
    receiver = clients.target.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: amount}, kyc_data=clients.stub.new_kyc_data())
    payment = sender.send_payment(currency, amount, payee=payment_uri.intent(hrp).account_id)

    sender.wait_for_event("updated_transaction", id=payment.id, status=Transaction.Status.completed)
    sender.wait_for_balance(currency, 0)
    receiver.wait_for_balance(currency, travel_rule_threshold)


def test_account_balance_validation_should_exclude_canceled_transactions(clients, currency, travel_rule_threshold, hrp):
    amount = travel_rule_threshold
    receiver = clients.target.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: amount}, kyc_data=clients.target.new_reject_kyc_data())
    # payment should be rejected during offchain kyc data exchange
    payment = sender.send_payment(currency, amount, payee=payment_uri.intent(hrp).account_id)

    sender.wait_for_event("updated_transaction", id=payment.id, status=Transaction.Status.canceled)

    sender.send_payment(currency, travel_rule_threshold - 1, payment_uri.intent(hrp).account_id)

    receiver.wait_for_balance(currency, travel_rule_threshold - 1)
    sender.wait_for_balance(currency, 1)


@pytest.mark.parametrize(
    "amount",
    [
        0,
        1,
        1_000_000_000,
        1_000_000_000_000,
    ],
)
def test_internal_transfer(clients, currency, amount, hrp):
    receiver = clients.stub.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: amount})

    index = len(sender.events())

    payment = sender.send_payment(currency, amount, payee=payment_uri.intent(hrp).account_id)
    assert payment.amount == amount
    assert payment.payee == payment_uri.intent(hrp).account_id

    sender.wait_for_event("updated_transaction", start_index=index, id=payment.id, status=Transaction.Status.completed)

    sender.wait_for_balance(currency, 0)
    receiver.wait_for_balance(currency, amount)
