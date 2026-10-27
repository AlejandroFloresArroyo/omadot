from omadot import AEC_SINK, AEC_SOURCE, aec_route


def test_routes_through_echo_cancel_when_both_nodes_exist():
    assert aec_route({AEC_SOURCE, "alsa_input.usb"}, {AEC_SINK}) == {
        "PULSE_SOURCE": AEC_SOURCE,
        "PULSE_SINK": AEC_SINK,
    }


def test_falls_back_to_the_default_devices_when_echo_cancel_is_missing():
    # A source that does not exist makes PortAudio fail to open the mic: Omi would be deaf.
    assert aec_route({"alsa_input.usb"}, {AEC_SINK}) == {}
    assert aec_route({AEC_SOURCE}, set()) == {}
