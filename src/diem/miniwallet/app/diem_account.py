# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Tuple, Optional
from .models import Transaction
from ... import jsonrpc, offchain, identifier, stdlib, utils, txnmetadata, LocalAccount


@dataclass
class DiemAccount:
    account: LocalAccount
    client: jsonrpc.Client
    retry: Optional[jsonrpc.Retry] = field(default_factory=lambda: jsonrpc.Retry(3, 0.1, jsonrpc.JsonRpcError))

    def general_metadata(self, from_subaddress: bytes, payee: str) -> Tuple[bytes, bytes]:
        to_account, to_subaddress = identifier.decode_account(payee, self.account.hrp)
        return (txnmetadata.general_metadata(from_subaddress, to_subaddress), b"")

    def travel_metadata(self, cmd: offchain.PaymentCommand) -> Tuple[bytes, bytes]:
        metadata = cmd.travel_rule_metadata(self.account.hrp)
        return (metadata, bytes.fromhex(str(cmd.payment.recipient_signature)))

    def submit_p2p(self, txn: Transaction, metadata: Tuple[bytes, bytes]) -> str:
        to_account, to_subaddress = identifier.decode_account(str(txn.payee), self.account.hrp)
        script = stdlib.encode_peer_to_peer_with_metadata_script(
            currency=utils.currency_code(txn.currency),
            amount=txn.amount,
            payee=to_account,
            metadata=metadata[0],
            metadata_signature=metadata[1],
        )
        return self.account.submit_txn(self.client, script).bcs_serialize().hex()