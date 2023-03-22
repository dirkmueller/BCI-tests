"""Tests for the Go language container."""
import re

import pytest
from pytest_container import GitRepositoryBuild
from pytest_container import Version
from pytest_container.container import ContainerData
from pytest_container.runtime import LOCALHOST

from bci_tester.data import BASE_CONTAINER
from bci_tester.data import GO_1_19_CONTAINER
from bci_tester.data import GOLANG_CONTAINERS
from bci_tester.runtime_choice import DOCKER_SELECTED

#: Maximum go container size in Bytes
GOLANG_MAX_CONTAINER_SIZE_ON_DISK = 1181116006  # 1.1GB uncompressed

CONTAINER_IMAGES = GOLANG_CONTAINERS


def test_go_size(auto_container, container_runtime):
    """Ensure that the go base container is below the size specified in
    :py:const:`GOLANG_MAX_CONTAINER_SIZE_ON_DISK`.

    """
    assert (
        container_runtime.get_image_size(auto_container.image_url_or_id)
        < GOLANG_MAX_CONTAINER_SIZE_ON_DISK
    )


def test_go_version(auto_container):
    """Check that the environment variable ``GOLANG_VERSION`` matches the output of
    :command:`go version`

    """
    assert auto_container.connection.check_output(
        "echo $GOLANG_VERSION"
    ) in auto_container.connection.check_output("go version")


@pytest.mark.parametrize(
    "container_git_clone",
    [
        GitRepositoryBuild(
            repository_url="https://github.com/weaveworks/kured.git",
            repository_tag="1.9.2",
            build_command="make cmd/kured/kured",
        ).to_pytest_param(),
    ],
    indirect=["container_git_clone"],
)
def test_build_kured(auto_container_per_test, container_git_clone):
    """Try to build `kured <https://github.com/weaveworks/kured.git>`_ inside the
    container with :command:`make` pre-installed.

    """
    auto_container_per_test.connection.run_expect(
        [0], container_git_clone.test_command
    )


def test_go_get_binary_in_path(auto_container_per_test):
    """Check that binaries installed via ``go install`` can be invoked (i.e. are in
    the ``$PATH``).

    """
    auto_container_per_test.connection.run_expect(
        [0], "go install github.com/tylertreat/comcast@latest"
    )
    assert (
        "Comcast"
        in auto_container_per_test.connection.run_expect(
            [0], "comcast -version"
        ).stdout
    )


@pytest.mark.parametrize("container", [BASE_CONTAINER], indirect=True)
def test_base_PATH_present(auto_container, container):
    """Regression test that we did not accidentally omit parts of ``$PATH`` that are
    present in he base container in the golang containers.

    """
    path_in_go_container = auto_container.connection.run_expect(
        [0], "echo $PATH"
    ).stdout.strip()
    path_in_base_container = container.connection.run_expect(
        [0], "echo $PATH"
    ).stdout.strip()
    assert path_in_base_container in path_in_go_container


@pytest.mark.parametrize(
    "container_git_clone",
    [
        GitRepositoryBuild(
            repository_url="https://github.com/Code-Hex/go-generics-cache.git",
            repository_tag="v1.0.1",
            build_command="go test ./...",
        )
    ],
    indirect=True,
)
def test_build_generics_cache(
    auto_container_per_test: ContainerData, container_git_clone
):
    """Test generics by running the tests of `go-generics-cache
    <https://github.com/Code-Hex/go-generics-cache>`_ inside the
    container. Generics are only supported for go 1.18+.

    """
    if Version.parse(
        auto_container_per_test.connection.check_output("echo $GOLANG_VERSION")
    ) < Version(1, 18):
        pytest.skip("Generics are only supported by go 1.18+")

    auto_container_per_test.connection.run_expect(
        [0], container_git_clone.test_command
    )


@pytest.mark.parametrize(
    "container,go_version",
    [
        pytest.param(
            GO_1_19_CONTAINER, Version(1, 19), marks=GO_1_19_CONTAINER.marks
        )
    ],
    indirect=["container"],
)
@pytest.mark.parametrize(
    "host_git_clone",
    [
        GitRepositoryBuild(
            repository_url="https://github.com/rancher/rancher",
        ).to_pytest_param()
    ],
    indirect=["host_git_clone"],
)
@pytest.mark.skipif(
    not DOCKER_SELECTED, reason="Dapper only works with docker"
)
@pytest.mark.skipif(
    LOCALHOST.system_info.arch not in ("x86_64", "aarch64", "s390x"),
    reason=f"{LOCALHOST.system_info.arch} is not supported to build rancher",
)
def test_rancher_build(
    host, host_git_clone, dapper, container: ContainerData, go_version: Version
):
    """Regression test that we can build Rancher in the go container:

    - clone the `rancher/rancher <https://github.com/rancher/rancher>`_ repository
    - monkey patch their :file:`Dockerfile.dapper` replacing their container
      image with the url or id of the go container
    - run :command:`dapper build`

    This test is only enabled for docker (dapper does not support podman).
    """
    dest, git_repo = host_git_clone
    rancher_dir = dest / git_repo.repo_name
    with open(
        rancher_dir / "Dockerfile.dapper", "r", encoding="utf-8"
    ) as dapperfile:
        contents = dapperfile.read(-1)

    from_line_regex = re.compile(
        r"^from registry\.suse\.com/bci/golang:(?P<go_ver>.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    from_line = from_line_regex.match(contents)

    assert from_line and from_line.group(
        "go_ver"
    ), f"No valid FROM line found in Dockerfile.dapper: {contents}"
    assert Version.parse(from_line.group("go_ver")) == go_version, (
        f"Golang version mismatch between the go container ({go_version}) "
        + f"and the rancher Dockerfile.dapper ({from_line.group('go_ver')})"
    )

    with open(
        rancher_dir / "Dockerfile.dapper", "w", encoding="utf-8"
    ) as dapperfile:
        dapperfile.write(
            from_line_regex.sub(f"FROM {container.image_url_or_id}", contents)
        )

    host.run_expect([0], f"cd {rancher_dir} && {dapper} build")
