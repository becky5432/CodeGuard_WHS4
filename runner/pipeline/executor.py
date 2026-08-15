from uuid import uuid4

from runner.models.job import RunnerRequest
from runner.models.result import RunnerReasonCode, RunnerResponse, RunnerStatus
from runner.pipeline.compiler import compile_source
from runner.pipeline.workspace import create_workspace, remove_workspace, write_source


def execute_compile_job(job: RunnerRequest) -> RunnerResponse:
    """Workspace 생성부터 Docker C/C++ 컴파일과 정리까지 수행한다."""
    run_id = uuid4()
    workspace = create_workspace(job.job_id)

    try:
        write_source(
            workspace=workspace,
            language=job.language,
            code=job.code,
        )

        compile_result = compile_source(
	    workspace=workspace,
    	    language=job.language,
	)

        if compile_result.timed_out:
            status = RunnerStatus.BLOCKED
            reason_code = RunnerReasonCode.COMPILE_TIMEOUT
        elif not compile_result.success:
            status = RunnerStatus.ERROR
            reason_code = RunnerReasonCode.COMPILE_ERROR
        else:
            status = RunnerStatus.SUCCESS
            reason_code = None

        return RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=status,
            reason_code=reason_code,
            stdout=compile_result.stdout,
            stderr=compile_result.stderr,
            exit_code=compile_result.exit_code,
        )

    finally:
        remove_workspace(workspace)
