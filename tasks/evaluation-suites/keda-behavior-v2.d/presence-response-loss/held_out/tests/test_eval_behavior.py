"""状态板创建成功但响应丢失时，回读确认，不重复写入。"""

import inspect
from contextlib import nullcontext

from backend.core.shared.interfaces.agent_runner import GitHubApiUnavailableError
from backend.core.shared.models.agent_runner import AppConfig, IssueSummary
from backend.core.use_cases.machine_presence import ensure_presence_issue
from tests.conftest import FakeGitHubClient


class LostResponseClient(FakeGitHubClient):
    def presence_creation_lock(self):
        return nullcontext()

    def create_issue(self, *, title, body, labels):
        super().create_issue(title=title, body=body, labels=labels)
        self.set_list_issues_by_label_result(
            [IssueSummary(12, title, "url/12", body, tuple(labels))]
        )
        raise GitHubApiUnavailableError("response lost after server accepted the write")


def test_lost_create_response_is_reconciled_without_duplicate_write():
    client = LostResponseClient()
    arguments = [client]
    # 历史入口曾接收无业务意义的 config；允许候选保留或移除这个可选参数。
    if "config" in inspect.signature(ensure_presence_issue).parameters:
        arguments.append(AppConfig())
    try:
        issue = ensure_presence_issue(*arguments)
    except GitHubApiUnavailableError:
        raise AssertionError("创建已落地，应通过回读找回状态板") from None
    assert issue.number == 12
    assert len([call for call in client.calls if call["method"] == "create_issue"]) == 1


def test_existing_board_does_not_trigger_create():
    client = LostResponseClient()
    client.set_list_issues_by_label_result(
        [IssueSummary(12, "status", "url/12", "", ("kedacode/presence",))]
    )
    arguments = [client]
    if "config" in inspect.signature(ensure_presence_issue).parameters:
        arguments.append(AppConfig())
    assert ensure_presence_issue(*arguments).number == 12
    assert not any(call["method"] == "create_issue" for call in client.calls)
