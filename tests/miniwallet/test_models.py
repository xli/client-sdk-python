# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0


from dataclasses import replace
from diem.testing.miniwallet import KycSample
from diem import offchain


def test_match_kyc_data():
    ks = KycSample.gen("foo")
    obj = offchain.from_json(ks.soft_match, offchain.KycDataObject)
    assert ks.match_kyc_data("soft_match", obj)
    assert not ks.match_kyc_data("reject", obj)

    obj = replace(obj, legal_entity_name="hello")
    assert ks.match_kyc_data("soft_match", obj)

    obj = replace(obj, given_name="hello")
    assert not ks.match_kyc_data("soft_match", obj)
