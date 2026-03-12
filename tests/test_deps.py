from initiate.deps import normalize_import_to_package


def test_normalize_import_to_package_mapping() -> None:
    assert normalize_import_to_package("cv2") == "opencv-python"
    assert normalize_import_to_package("yaml") == "PyYAML"
    assert normalize_import_to_package("seaboard") == "seaborn"


def test_normalize_import_to_package_passthrough() -> None:
    assert normalize_import_to_package("fastapi") == "fastapi"
    assert normalize_import_to_package("pandas.io") == "pandas"
