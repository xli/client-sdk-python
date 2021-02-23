# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from diem.testing.miniwallet.app import store, PaymentCommand


def test_find_all_by_matching_property_values():
    s = store.InMemory()
    cmd = s.create(
        PaymentCommand, is_sender=True, account_id="1", reference_id="2", cid="3", payment_object={}, is_inbound=True
    )
    assert s.find_all(PaymentCommand, is_inbound=True, is_ready=False) == [cmd]
    assert s.find_all(PaymentCommand, is_inbound=False, is_ready=False) == []
