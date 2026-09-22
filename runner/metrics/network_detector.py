import subprocess


def detect_network_block(
    container_ip: str,
    start_time: float,
) -> bool:
    result = subprocess.run(
        [
            "journalctl",
            "-k",
            "--since", f"@{start_time - 1}",
            "--no-pager",
            "-o", "cat",
        ],
        capture_output=True,
        text=True,
    )

    target = f"SRC={container_ip}"

    return any(
        "CG_NETBLOCK" in line and target in line
        for line in result.stdout.splitlines()
    )