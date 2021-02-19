# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from typing import Type, List, Tuple, cast
from json.decoder import JSONDecodeError
from .store import InMemory, NotFoundError
from .diem_account import DiemAccount
from .models import T, ReceivePayment, Account, Transaction, Command, KycSamples
from .event_puller import EventPuller
from .json_input import JsonInput
from ... import jsonrpc, offchain, utils, LocalAccount
from ...offchain import PaymentCommand, KycDataObject, Status, AbortCode, CommandResponseObject, PaymentCommandObject


def kyc_data(name: str, app_name: str) -> KycDataObject:
    return offchain.individual_kyc_data(given_name=name, surname=app_name)


def kyc_samples(app_name: str) -> KycSamples:
    return KycSamples(
        minimum=offchain.to_json(kyc_data("Micro", app_name)),
        reject=offchain.to_json(kyc_data("Rock", app_name)),
        soft_match=offchain.to_json(kyc_data("Sand", app_name)),
        soft_reject=offchain.to_json(kyc_data("Salt", app_name)),
    )


class Base:
    def __init__(self, account: LocalAccount, client: jsonrpc.Client, name: str = "miniw") -> None:
        self.diem = DiemAccount(account, client)
        self.store = InMemory()
        self.offchain = offchain.Client(account.account_address, client, account.hrp)
        self.kyc_samples: KycSamples = kyc_samples(name)
        self.event_puller = EventPuller(client=client, store=self.store, hrp=account.hrp)
        self.event_puller.add(account.account_address)
        self.event_puller.head()
        self.subaddress_id = 1

    def _validate_kyc_data(self, name: str, val: str) -> None:
        try:
            offchain.from_json(val, KycDataObject)
        except (JSONDecodeError, offchain.types.FieldError) as e:
            raise ValueError("%r must be JSON-encoded KycDataObject: %s" % (name, e))

    def _validate_currency_code(self, name: str, val: str) -> None:
        try:
            self.offchain.validate_currency_code(val)
        except ValueError:
            raise ValueError("%r is invalid currency code: %s" % (name, val))

    def _validate_account_identifier(self, name: str, val: str) -> None:
        try:
            self.diem.account.decode_account_identifier(val)
        except ValueError as e:
            raise ValueError("%r is invalid account identifier: %s" % (name, e))

    def _validate_amount(self, name: str, val: int) -> None:
        if val < 0:
            raise ValueError("%r value must be greater than or equal to zero" % name)

    def _validate_account_balance(self, txn: Transaction) -> None:
        if txn.payee:
            balance = self._balance(str(txn.account_id), txn.currency)
            if balance < txn.amount:
                raise ValueError("account balance not enough: %s < %s" % (balance, txn.amount))

    def _balance(self, account_id: str, currency: str) -> int:
        txns = self.store.find_all(Transaction, account_id=account_id, currency=currency)
        return sum([t.balance_amount() for t in txns if t.status != "canceled"])

    def _gen_subaddress(self) -> bytes:
        self.subaddress_id += 1
        return self.subaddress_id.to_bytes(8, byteorder="big")

    def _to_offchain_command(self, cmd: Command) -> offchain.Command:
        req = cmd.request_object()
        if isinstance(req.command, PaymentCommandObject):
            return self.offchain.create_payment_command(req.cid, req.command.payment)
        raise ValueError("unsupported command type: %s" % req.command_type)

    def _txn_metadata(self, txn: Transaction) -> Tuple[bytes, bytes]:
        if txn.reference_id:
            cmd = self.store.find(Command, last=True, reference_id=txn.reference_id)
            return self.diem.travel_metadata(cast(PaymentCommand, self._to_offchain_command(cmd)))
        if txn.subaddress_hex:
            return self.diem.general_metadata(txn.subaddress(), str(txn.payee))
        raise ValueError("transaction is not ready for submit diem transaction: %s" % txn)


class OffChainAPI(Base):
    def offchain_api(self, request_sender_address: str, request_bytes: bytes) -> CommandResponseObject:
        cmd = self.offchain.process_inbound_request(request_sender_address, request_bytes)
        with self.store.transaction:
            getattr(self, "_handle_offchain_%s" % utils.to_snake(cmd))(cmd)
        return offchain.reply_request(cid=cmd.id())

    def jws_serialize(self, resp: CommandResponseObject) -> bytes:
        return offchain.jws.serialize(resp, self.diem.account.compliance_key.sign)

    def _handle_offchain_payment_command(self, new_cmd: PaymentCommand) -> None:
        try:
            cmd = self.store.find(Command, last=True, reference_id=new_cmd.reference_id())
            payment_command = self._to_offchain_command(cmd)
            account_id = cmd.account_id
        except NotFoundError:
            payment_command = None
            subaddress = utils.hex(new_cmd.my_subaddress(self.diem.account.hrp))
            account_id = self.store.find(ReceivePayment, subaddress_hex=subaddress).account_id

        if new_cmd != payment_command:
            new_cmd.validate(payment_command)
            self._create_account_command(account_id, new_cmd)

    def _process_offchain_commands(self) -> None:
        cmds = {cmd.reference_id: cmd for cmd in self.store.find_all(Command)}
        for cmd in cmds.values():
            offchain_cmd = self._to_offchain_command(cmd)
            action = offchain_cmd.follow_up_action()
            if not action:
                continue
            new_cmd = getattr(self, "_process_offchain_action_%s" % action.value)(cmd.account_id, offchain_cmd)
            if new_cmd:
                self.offchain.send_command(new_cmd, self.diem.account.compliance_key.sign)
                self._create_account_command(cmd.account_id, new_cmd)

    def _process_offchain_action_evaluate_kyc_data(self, account_id: str, cmd: PaymentCommand) -> offchain.Command:
        op_kyc_data = cmd.opponent_actor_obj().kyc_data
        if op_kyc_data is None or self.kyc_samples.match_kyc_data("reject", op_kyc_data):
            return self._new_reject_kyc_data(cmd, "KYC data is rejected")
        elif self.kyc_samples.match_any_kyc_data(["soft_match", "soft_reject"], op_kyc_data):
            return cmd.new_command(status=Status.soft_match)
        return self._payment_command_ready_for_settlement(account_id, cmd)

    def _process_offchain_action_clear_soft_match(self, account_id: str, cmd: PaymentCommand) -> offchain.Command:
        return cmd.new_command(additional_kyc_data="{%r: %r}" % ("account_id", account_id))

    def _process_offchain_action_review_kyc_data(self, account_id: str, cmd: PaymentCommand) -> offchain.Command:
        op_kyc_data = cmd.opponent_actor_obj().kyc_data
        if op_kyc_data is None or self.kyc_samples.match_kyc_data("soft_reject", op_kyc_data):
            return self._new_reject_kyc_data(cmd, "KYC data review result is reject")
        return self._payment_command_ready_for_settlement(account_id, cmd)

    def _process_offchain_action_submit_transaction(self, account_id: str, cmd: PaymentCommand) -> None:
        txn = self.store.find(Transaction, reference_id=cmd.reference_id())
        if txn.signed_transaction is None:
            txn.signed_transaction = self.diem.submit_p2p(txn, self._txn_metadata(txn))

    def _new_reject_kyc_data(self, cmd: PaymentCommand, msg: str) -> offchain.Command:
        return cmd.new_command(status=Status.abort, abort_code=AbortCode.reject_kyc_data, abort_message=msg)

    def _payment_command_ready_for_settlement(self, account_id: str, cmd: PaymentCommand) -> offchain.Command:
        if cmd.is_sender():
            return cmd.new_command(status=Status.ready_for_settlement)

        sig_msg = cmd.travel_rule_metadata_signature_message(self.diem.account.hrp)
        sig = self.diem.account.compliance_key.sign(sig_msg).hex()
        kyc_data = self.store.find(Account, id=account_id).kyc_data_object()
        return cmd.new_command(recipient_signature=sig, kyc_data=kyc_data, status=Status.ready_for_settlement)

    def _create_account_command(self, account_id: str, cmd: PaymentCommand) -> None:
        req_json = offchain.to_json(cmd.new_request())
        self.store.create(Command, account_id=account_id, reference_id=cmd.reference_id(), request_json=req_json)
        if cmd.is_abort() and cmd.is_sender():
            txn = self.store.find(Transaction, reference_id=cmd.reference_id())
            txn.status = "canceled"
            txn.cancel_reason = "exchange kyc data abort"


class App(OffChainAPI):
    def sync(self) -> None:
        with self.store.transaction:
            self._process_offchain_commands()
            self.event_puller.fetch(self.event_puller.save_payment_txn)
            self._execute_send_payment_txns()

    def get_resources(self, typ: Type[T]) -> List[T]:
        return self.store.find_all(typ)

    def create_resource(self, typ: Type[T], data: JsonInput) -> T:
        with self.store.transaction:
            return getattr(self, "_create_resource_%s" % utils.to_snake(typ))(data)

    def _create_resource_account(self, data: JsonInput) -> Account:
        return self.store.create(
            Account,
            kyc_data=data.get("kyc_data", str, self._validate_kyc_data),
        )

    def _create_resource_transaction(self, data: JsonInput) -> Transaction:
        account = self.store.find(Account, id=data.get("account_id", str))
        payee = data.get_nullable("payee", str, self._validate_account_identifier)
        return self.store.create(
            Transaction,
            account_id=account.id,
            currency=data.get("currency", str, self._validate_currency_code),
            amount=data.get("amount", int, self._validate_amount),
            payee=payee,
            status="pending" if payee else "completed",
            before_create=self._validate_account_balance,
        )

    def _create_resource_receive_payment(self, data: JsonInput) -> ReceivePayment:
        subaddress = self._gen_subaddress()
        return self.store.create(
            ReceivePayment,
            account_id=self.store.find(Account, id=data.get("account_id", str)).id,
            subaddress_hex=subaddress.hex(),
            account_identifier=self.diem.account.account_identifier(subaddress),
        )

    def _execute_send_payment_txns(self) -> None:
        for txn in self.store.find_all(Transaction, status="pending"):
            if txn.payee is not None:
                self._execute_send_payment_txn(txn)

    def _execute_send_payment_txn(self, txn: Transaction) -> None:
        if self.offchain.is_my_account_id(str(txn.payee)):
            self._send_internal_payment_txn(txn)
        else:
            self._send_external_payment_txn(txn)

    def _send_internal_payment_txn(self, txn: Transaction) -> None:
        _, payee_subaddress = self.diem.account.decode_account_identifier(str(txn.payee))
        rp = self.store.find(ReceivePayment, subaddress_hex=utils.hex(payee_subaddress))
        self.store.create(
            Transaction, account_id=rp.account_id, currency=txn.currency, amount=txn.amount, status="completed"
        )
        txn.status = "completed"

    def _send_external_payment_txn(self, txn: Transaction) -> None:
        if txn.signed_transaction:
            try:
                diem_txn = self.diem.client.wait_for_transaction(str(txn.signed_transaction))
                txn.status = "completed"
                txn.diem_transaction_version = diem_txn.version
            except jsonrpc.WaitForTransactionTimeout:
                pass  # need continue to wait
            except (jsonrpc.TransactionHashMismatchError):
                txn.signed_transaction = self.diem.submit_p2p(txn, self._txn_metadata(txn))
            except (jsonrpc.TransactionExpired, jsonrpc.TransactionExecutionFailed) as e:
                txn.status = "canceled"
                txn.cancel_reason = "something went wrong with transaction execution: %s" % e
        else:
            self._start_external_payment_txn(txn)

    def _start_external_payment_txn(self, txn: Transaction) -> None:
        if txn.subaddress_hex:
            return
        txn.subaddress_hex = self._gen_subaddress().hex()
        if self.offchain.is_under_dual_attestation_limit(txn.currency, txn.amount):
            txn.signed_transaction = self.diem.submit_p2p(txn, self._txn_metadata(txn))
        else:
            account = self.store.find(Account, id=txn.account_id)
            command = PaymentCommand.init(
                sender_account_id=self.diem.account.account_identifier(txn.subaddress()),
                sender_kyc_data=account.kyc_data_object(),
                currency=txn.currency,
                amount=txn.amount,
                receiver_account_id=str(txn.payee),
            )
            txn.reference_id = command.reference_id()
            self.offchain.send_command(command, self.diem.account.compliance_key.sign)
            self._create_account_command(account.id, command)
