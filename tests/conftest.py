import pytest

from library_factory import ffmpeg_exe, make_libraries

needs_ffmpeg = pytest.mark.skipif(ffmpeg_exe() is None, reason='ffmpeg is needed to build test libraries')


@pytest.fixture(scope='session')
def libraries(tmp_path_factory):
    """Two sample libraries, built once and shared read-only by all tests."""
    if ffmpeg_exe() is None:
        pytest.skip('ffmpeg is needed to build test libraries')
    base = tmp_path_factory.mktemp('libs')
    a, b, xml = make_libraries(str(base))
    return dict(base=str(base), a=a, b=b, xml=xml)


@pytest.fixture
def project(libraries, tmp_path):
    from musicmerge.core.project import Project
    p = Project(str(tmp_path / 'Merged'))
    p.set_libraries([{'path': libraries['a'], 'xml': libraries['xml']},
                     {'path': libraries['b'], 'xml': ''}])
    p.save()
    return p


@pytest.fixture(scope='session')
def fpcalc():
    from musicmerge.core.fingerprint import find_fpcalc
    path = find_fpcalc()
    if not path:
        pytest.skip('fpcalc not installed (python -m musicmerge fpcalc --download)')
    return path
