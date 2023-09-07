import sys
import pytest
from pathlib import Path

sys.path.append("..")
from markFeatureWriter import *

TEST_DIR = Path(__file__).parent


def read_file(path):
    '''
    Read a file, split lines into a list, close the file.
    '''

    with open(path, 'r', encoding='utf-8') as f:
        data = f.read().splitlines()
    return data


# def test_WhichApp():
#     assert WhichApp().appName == 'Defcon'
#     # import __mocks__ as flsys ???
#     # assert WhichApp().appName == 'FontLab'

def test_get_args():
    argparse_args = vars(get_args(['dummy']))  # args through argparse
    dummy_args = Defaults().__dict__  # hard-coded dummy arguments
    dummy_args['input_file'] = 'dummy'
    assert argparse_args == dummy_args


def test_no_group():
    args = Defaults()
    args.input_file = TEST_DIR / 'mfw_no_group.ufo'
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        MarkFeatureWriter(args)
    assert pytest_wrapped_e.type == SystemExit


def test_full_run():
    args = Defaults()
    ufo_path = TEST_DIR / 'mfw_simple.ufo'
    example_feature = read_file(TEST_DIR / 'mfw_simple.fea')
    args.input_file = ufo_path
    MarkFeatureWriter(args)
    assert read_file(TEST_DIR / 'mark.fea') == example_feature


if __name__ == '__main__':
    test_full_run()
