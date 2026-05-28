import json

from main import _json_additional_media, _json_message_ids


def test_single_media_message_ids_store_empty_json_array():
    assert _json_message_ids([]) == "[]"


def test_media_group_message_ids_preserve_order():
    assert json.loads(_json_message_ids([103, 104, 105])) == [103, 104, 105]


def test_malformed_message_ids_are_rejected():
    assert json.loads(_json_message_ids([101, "bad", None, "102"])) == [101, 102]


def test_additional_media_payload_is_valid_json_and_ordered():
    payload = _json_additional_media(["a", "b"], ["photo", "video"])
    assert json.loads(payload) == [["a", "photo"], ["b", "video"]]
