"""Tests for ``openftir.instruments.summit.protocol`` (build/parse XML)."""

from __future__ import annotations

import pytest

from openftir.instruments.summit.protocol import ProtocolError, build_get, build_set, parse_reply


def test_build_get_single_key():
    assert build_get(("benchModel",)) == "<cmd><get><benchModel/></get></cmd>"


def test_build_get_multiple_keys_preserves_order():
    xml = build_get(("apertureCfg", "bpTable", "detGainCfg"))
    assert xml == "<cmd><get><apertureCfg/><bpTable/><detGainCfg/></get></cmd>"


def test_build_set_formats_int_float_str():
    xml = build_set({"peakPos": 6154, "fwdVel": 0.4747, "sdp": 12426})
    assert xml == "<cmd><set><peakPos>6154</peakPos><fwdVel>0.4747</fwdVel><sdp>12426</sdp></set></cmd>"


def test_build_set_float_with_integer_value_has_no_decimal():
    # peakPos/sdp etc. are logically ints but might arrive as float; Paradigm
    # sends "6154" not "6154.0".
    xml = build_set({"collectState": 1.0})
    assert xml == "<cmd><set><collectState>1</collectState></set></cmd>"


def test_build_set_bool_as_0_1():
    xml = build_set({"bidirScan": False})
    assert xml == "<cmd><set><bidirScan>0</bidirScan></set></cmd>"


# -- parsing: literal templates from docs/summit-protocol.md "Message grammar" --


def test_parse_get_reply_template():
    reply = parse_reply("<reply><a>val</a><b>val</b></reply>")
    assert reply.values == {"a": "val", "b": "val"}
    assert reply.not_recognized == []
    assert reply.errors == []


def test_parse_set_echo_reply_template():
    reply = parse_reply("<reply><a>val</a></reply>")
    assert reply.values == {"a": "val"}


def test_parse_not_recognized_template():
    reply = parse_reply("<reply><notRecognized>sampleshuttle</notRecognized></reply>")
    assert reply.not_recognized == ["sampleshuttle"]
    assert reply.values == {}


def test_parse_error_template():
    reply = parse_reply("<reply><error><key/></error></reply>")
    assert reply.errors == ["key"]


def test_parse_benchmsg_event_template():
    reply = parse_reply("<benchMsg><motorsDone>0</motorsDone></benchMsg>", root_tag="benchMsg")
    assert reply.get_int("motorsDone") == 0


# -- parsing: realistic combined replies built from "Values seen" table --


def test_parse_scalar_values():
    xml = (
        "<reply><benchModel>142</benchModel><firmVer>1.18</firmVer>"
        "<laserFreq>11685.125977</laserFreq><maxRes>0.5</maxRes></reply>"
    )
    reply = parse_reply(xml)
    assert reply.get("benchModel") == "142"
    assert reply.get_float("laserFreq") == pytest.approx(11685.125977)
    assert reply.get_float("maxRes") == pytest.approx(0.5)
    assert reply.get_int("missing", default=-1) == -1


def test_parse_repeated_detgain_entries_inside_cfg():
    xml = (
        "<reply><detGainCfg><numEntries>4</numEntries>"
        "<detGain>1</detGain><detGain>2</detGain><detGain>4</detGain><detGain>8</detGain>"
        "</detGainCfg></reply>"
    )
    reply = parse_reply(xml)
    cfg = reply.values["detGainCfg"]
    assert cfg["numEntries"] == "4"
    assert cfg["detGain"] == ["1", "2", "4", "8"]


def test_parse_repeated_beampath_entries_in_bptable():
    xml = (
        "<reply><bpTable>"
        "<beampath><beampathId>1</beampathId><detector>12</detector></beampath>"
        "<beampath><beampathId>13</beampathId><detector>4</detector></beampath>"
        "</bpTable></reply>"
    )
    reply = parse_reply(xml)
    beampaths = reply.values["bpTable"]["beampath"]
    assert beampaths == [
        {"beampathId": "1", "detector": "12"},
        {"beampathId": "13", "detector": "4"},
    ]


def test_parse_min_max_step_range():
    xml = "<reply><sdpCfg><min>256</min><max>68000</max><step>1</step></sdpCfg></reply>"
    reply = parse_reply(xml)
    assert reply.values["sdpCfg"] == {"min": "256", "max": "68000", "step": "1"}


def test_parse_mixed_values_and_not_recognized():
    xml = "<reply><benchModel>142</benchModel><notRecognized>sampleshuttle</notRecognized></reply>"
    reply = parse_reply(xml)
    assert reply.values == {"benchModel": "142"}
    assert reply.not_recognized == ["sampleshuttle"]


def test_parse_malformed_xml_raises_protocol_error():
    with pytest.raises(ProtocolError):
        parse_reply("<reply><unterminated>")


def test_parse_wrong_root_tag_raises():
    with pytest.raises(ProtocolError):
        parse_reply("<reply><a>1</a></reply>", root_tag="benchMsg")
