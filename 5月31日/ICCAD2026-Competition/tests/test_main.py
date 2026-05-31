from main import extract_case_info


def test_extract_case_name_from_release_prompt():
    case_name, log_name = extract_case_info(
        "This is the beginning of a new testcase. The case name is test01."
    )

    assert case_name == "test01"
    assert log_name == "test01.log"


def test_extract_case_name_from_original_prompt():
    case_name, log_name = extract_case_info(
        "This is the beginning of testcase case28. Please output a copy of the log into case28.log."
    )

    assert case_name == "case28"
    assert log_name == "case28.log"
