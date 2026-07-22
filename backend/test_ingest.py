import os
import tempfile
import pandas as pd
from app import create_app
from app.extensions import db
from app.services.excel_ingest import ingest_excel
from app.services.dashboard_service import build_dashboard

app = create_app()

FILE_ROW_1 = "storage/uploads/e0580a09_Merchant Settlement Report 2026-06-23.xlsx"
FILE_ROW_3 = "/home/kunjan/Downloads/Merchant Settlement Report 2026-06-24.xlsx"


def test_ingest_row_1_header():
    with app.app_context():
        batch = ingest_excel(FILE_ROW_1)
        dash = build_dashboard(batch.id)
        assert dash["totals"]["total_transactions"] > 0
        assert dash["totals"]["total_aggregators"] > 0
        print(f"Row 1 header test passed: Batch '{batch.name}' (id={batch.id}), {dash['totals']['total_transactions']} txns, {dash['totals']['total_aggregators']} aggregators")


def test_ingest_row_3_header():
    with app.app_context():
        batch = ingest_excel(FILE_ROW_3)
        dash = build_dashboard(batch.id)
        assert dash["totals"]["total_transactions"] > 0
        assert dash["totals"]["total_aggregators"] > 0
        print(f"Row 3 header test passed: Batch '{batch.name}' (id={batch.id}), {dash['totals']['total_transactions']} txns, {dash['totals']['total_aggregators']} aggregators")


def test_upload_invalid_file_returns_422():
    with app.test_client() as client:
        # Create a dummy excel missing required MID column
        df = pd.DataFrame({"Unknown Column": [1, 2, 3]})
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
            df.to_excel(tmp_path, index=False)

        try:
            with open(tmp_path, "rb") as f:
                res = client.post("/api/batches/upload", data={"file": (f, "invalid.xlsx")}, content_type="multipart/form-data")
            assert res.status_code == 422
            json_data = res.get_json()
            assert "error" in json_data
            print("Invalid file test passed: HTTP 422 returned with error:", json_data["error"])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    test_ingest_row_1_header()
    test_ingest_row_3_header()
    test_upload_invalid_file_returns_422()
    print("ALL INGESTION TESTS PASSED SUCCESSFULLY!")
