from tests.scenarios.barge_in import run_barge_in_test
from tests.scenarios.basic_pipeline import run_basic_pipeline_test
from tests.scenarios.cancel_no_response import run_cancel_no_response_test
from tests.scenarios.clear_buffer import run_clear_buffer_test
from tests.scenarios.delete_item import run_delete_item_test
from tests.scenarios.manual_commit import run_manual_commit_test
from tests.scenarios.manual_conversation import run_manual_conversation_test
from tests.scenarios.multi_round import run_multi_round_test
from tests.scenarios.no_response_token import run_no_response_token_test
from tests.scenarios.no_response_token_disabled import run_no_response_token_disabled_test
from tests.scenarios.noise_gate import run_noise_gate_test
from tests.scenarios.response_cancel import run_response_cancel_test
from tests.scenarios.session_update import run_session_update_test
from tests.scenarios.truncate import run_truncate_test

SCENARIOS = [
    ("basic_pipeline", run_basic_pipeline_test),
    ("response_cancel", run_response_cancel_test),
    ("truncate", run_truncate_test),
    ("cancel_no_response", run_cancel_no_response_test),
    ("barge_in", run_barge_in_test),
    ("session_update", run_session_update_test),
    ("manual_conversation", run_manual_conversation_test),
    ("delete_item", run_delete_item_test),
    ("clear_buffer", run_clear_buffer_test),
    ("multi_round", run_multi_round_test),
    ("manual_commit", run_manual_commit_test),
    ("no_response_token", run_no_response_token_test),
    ("no_response_token_disabled", run_no_response_token_disabled_test),
    ("noise_gate", run_noise_gate_test),
]

__all__ = ["SCENARIOS"]
