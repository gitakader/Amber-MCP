from amber_mcp import redact


XML = ('<node text="Rakin Uddin" resource-id="com.amberit.dialer:id/name" '
       'content-desc="+8801712345678"/> <node text="৳4,250.75"/>')


def test_known_numbers_are_replaced_not_left_because_they_look_like_content():
    out = redact.text(XML, secrets=["+8801712345678"])
    assert "+8801712345678" not in out
    assert redact.NUMBER_TOKEN in out


def test_bangladeshi_mobile_shapes_are_scrubbed_by_pattern():
    # A captured list holds numbers we were never told about.
    out = redact.text('text="01799887766" text="01911223344"')
    assert "01799887766" not in out and "01911223344" not in out
    assert out.count(redact.NUMBER_TOKEN) == 2


def test_balance_amounts_are_scrubbed():
    out = redact.text('available balance ৳4,250.75')
    assert "4,250.75" not in out
    assert redact.BALANCE_TOKEN in out


def test_named_contacts_are_replaced_when_a_name_list_is_given():
    out = redact.text(XML, secrets=["Rakin Uddin"])
    assert "Rakin Uddin" not in out
    assert redact.NAME_TOKEN in out


def test_resource_ids_survive_because_they_are_the_whole_point():
    out = redact.text(XML, secrets=["Rakin Uddin", "+8801712345678"])
    assert 'resource-id="com.amberit.dialer:id/name"' in out


def test_text_without_secrets_is_unchanged():
    plain = "com.amberit.dialer:id/dialpad_one text=\"1\""
    assert redact.text(plain) == plain


def test_an_empty_or_whitespace_input_is_not_an_error():
    assert redact.text("") == ""
    assert redact.text("   ") == "   "
