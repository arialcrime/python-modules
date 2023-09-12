import defcon
import sys
from pathlib import Path

sys.path.append("..")
from kernFeatureWriter import *

TEST_DIR = Path(__file__).parent


def read_file(path):
    '''
    Read a file, split lines into a list, close the file.
    '''

    with open(path, 'r', encoding='utf-8') as f:
        data = f.read().splitlines()
    return data


def test_WhichApp():
    assert WhichApp().appName == 'Defcon'
    # import __mocks__ as flsys ???
    # assert WhichApp().appName == 'FontLab'


def test_get_args():
    argparse_args = vars(get_args(['dummy']))  # args through argparse
    dummy_args = Defaults().__dict__  # hard-coded dummy arguments
    dummy_args['input_file'] = 'dummy'
    assert argparse_args == dummy_args


def test_full_run():
    args = Defaults()
    ufo_path = TEST_DIR / 'kern_example.ufo'
    tmp_feature = TEST_DIR / 'tmp_kern_example.fea'
    example_feature = read_file(TEST_DIR / 'kern_example.fea')
    args.input_file = ufo_path
    args.output_file = tmp_feature
    f = defcon.Font(ufo_path)
    run(f, args)
    assert read_file(tmp_feature) == example_feature
    tmp_feature.unlink()


def test_subtable():
    '''
    test writing a file with subtable breaks
    '''
    args = Defaults()
    ufo_path = TEST_DIR / 'kern_example.ufo'
    tmp_feature = TEST_DIR / 'tmp_kern_example_subs.fea'
    example_feature = read_file(TEST_DIR / 'kern_example_subs.fea')
    args.input_file = ufo_path
    args.write_subtables = True
    args.subtable_size = 128
    args.output_file = tmp_feature
    f = defcon.Font(ufo_path)
    run(f, args)
    assert read_file(tmp_feature) == example_feature
    tmp_feature.unlink()


def test_dissolve():
    '''
    test dissolving single-glyph groups
    '''
    args = Defaults()
    ufo_path = TEST_DIR / 'kern_AV.ufo'
    tmp_feature_undissolved = TEST_DIR / 'tmp_kern_AV_undissolved.fea'
    tmp_feature_dissolved = TEST_DIR / 'tmp_kern_AV_dissolved.fea'
    example_feature_undissolved = read_file(
        TEST_DIR / 'kern_AV_undissolved.fea')
    example_feature_dissolved = read_file(
        TEST_DIR / 'kern_AV_dissolved.fea')
    args.input_file = ufo_path
    args.output_file = tmp_feature_undissolved
    f = defcon.Font(ufo_path)
    run(f, args)
    assert read_file(tmp_feature_undissolved) == example_feature_undissolved
    args.dissolve_single = True
    args.output_file = tmp_feature_dissolved
    run(f, args)
    assert read_file(tmp_feature_dissolved) == example_feature_dissolved
    tmp_feature_undissolved.unlink()
    tmp_feature_dissolved.unlink()


def test_case_01():
    '''
    test a kerning exception of a single member of a left-side group
    (Adieresis for the A-group, Oslash for the O-group) to a right-side item.
    '''
    args = Defaults()
    ufo_path = TEST_DIR / 'kern_case_01.ufo'
    tmp_feature = TEST_DIR / 'tmp_case_01.fea'
    example_feature = read_file(TEST_DIR / 'kern_case_01.fea')
    args.input_file = ufo_path
    args.output_file = tmp_feature
    f = defcon.Font(ufo_path)
    run(f, args)
    assert read_file(tmp_feature) == example_feature
    tmp_feature.unlink()
