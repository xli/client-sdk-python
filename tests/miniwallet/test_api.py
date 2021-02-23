# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from diem.testing.miniwallet import Account, Transaction
from diem import identifier
import pytest, requests, time, json


def test_account_resource_create_with_kyc_data_and_balances(clients, currency):
    client = clients.stub
    kyc_data = client.new_kyc_data()
    account = client.create_account(kyc_data=kyc_data, balances={currency: 100})
    assert account.id
    assert account.kyc_data == kyc_data
    assert account.balances() == {currency: 100}
    assert account.balance(currency) == 100


def test_account_resource_creation_event(clients, currency):
    client = clients.stub
    before_timestamp = int(time.time() * 1000)
    account = client.create_account()
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


def test_account_resource_create_without_balance(clients):
    account = clients.app.create_account()
    assert account.balances() == {}


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
def test_account_resource_creation_errors(clients, currency, err_msg, kyc_data, balances):
    client = clients.stub
    if kyc_data == "sample":
        kyc_data = client.new_kyc_data()

    with pytest.raises(requests.exceptions.HTTPError, match="400 Client Error") as einfo:
        client.create("/accounts", kyc_data=kyc_data, balances=balances)
    assert err_msg in einfo.value.response.text


def test_payment_uri_resource(clients, hrp):
    account = clients.stub.create_account()
    assert len(account.events()) == 1
    ret = account.create_payment_uri()
    assert ret.account_id == account.id
    assert ret.subaddress_hex
    assert ret.intent(hrp).account_id
    address, subaddress = identifier.decode_account(ret.intent(hrp).account_id, hrp)
    assert address
    assert subaddress
    assert ret.subaddress_hex == subaddress.hex()
    assert len(account.events()) == 2
    assert account.events()[1].type == "created_payment_uri"


def test_send_payment_and_events(clients, hrp, currency):
    receiver = clients.app.create_account()
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
    sender = clients.app.create_account({currency: amount})
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
    sender1 = clients.app.create_account({currency: amount})
    sender1.send_payment(currency, amount, payment_uri.intent(hrp).account_id)

    sender2 = clients.app.create_account({currency: amount})
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
    receiver = clients.app.create_account()
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
    receiver = clients.app.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: amount}, kyc_data=clients.stub.new_kyc_data())
    payment = sender.send_payment(currency, amount, payee=payment_uri.intent(hrp).account_id)

    sender.wait_for_event("updated_transaction", id=payment.id, status=Transaction.Status.completed)
    sender.wait_for_balance(currency, 0)
    receiver.wait_for_balance(currency, travel_rule_threshold)


def test_account_balance_validation_should_exclude_canceled_transactions(clients, currency, travel_rule_threshold, hrp):
    amount = travel_rule_threshold
    receiver = clients.app.create_account()
    payment_uri = receiver.create_payment_uri()
    sender = clients.stub.create_account({currency: amount}, kyc_data=clients.app.new_reject_kyc_data())
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
