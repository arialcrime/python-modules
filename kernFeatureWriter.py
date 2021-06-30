#!/usr/bin/env python3

'''
kernFeatureWriter.py 1.0 - Sept 2016

Rewrite of WriteFeaturesKernFDK.py, which will eventually be replaced by
this module. The main motivation for this were problems with kerning
subtable overflow.

Main improvements of this script compared to WriteFeaturesKernFDK.py:
-   can be called from the command line, with a UFO file as an argument
-   automatic subtable measuring
-   ability to dissolve single-glyph groups into glyph-pairs
    (this feature was written for subtable optimization)
-   identify glyph-to-glyph RTL kerning (requirement: all RTL glyphs
    are part of a catch-all @RTL_KERNING group)

To do:
-   write proper tests for individual functions.
    Some doctests were written, but not enough for all scenarios
-   measure the `mark` feature, which also contributes to the size of the
    GPOS table (and therefore indirectly influences kerning overflow)
-   test kerning integrity, to make sure referenced glyphs actually exist
    (and building binaries doesn't fail).

'''

import argparse
import itertools
import os
import time


group_RTL = 'RTL_KERNING'

tags_left = ['_LEFT', '_1ST', '_L_']
tags_right = ['_RIGHT', '_2ND', '_R_']

tag_ara = '_ARA'
tag_heb = '_HEB'
tag_RTL = '_RTL'
tag_exception = 'EXC_'
tag_ignore = '.cxt'


class Defaults(object):
    """
    default values
    These can later be overridden by argparse.
    """

    def __init__(self):

        # The default output filename
        self.output_file = 'kern.fea'

        # Default mimimum kerning value. This value is _inclusive_, which
        # means that pairs that equal this absolute value will NOT be
        # ignored/trimmed. Anything in range of +/- value will be trimmed.
        self.min_value = 3

        # The maximum possible subtable size is 2 ** 16 = 65536.
        # Since every other GPOS feature counts against that size, the
        # subtable size chosen needs to be quite a bit smaller.
        # 2 ** 14 has been a good value for Source Serif
        # (but failed for master_2, where 2 ** 13 was used)
        self.subtable_size = 2 ** 13

        # If 'False', trimmed pairs will not be processed and therefore
        # not be written to the output file.
        self.write_trimmed_pairs = False

        # Write subtables -- yes or no?
        self.write_subtables = False

        # Write time stamp in .fea file header?
        self.write_timestamp = False

        # Write single-element groups as glyphs?
        # (This has no influence on the output kerning data, but helps with
        # balancing subtables, and potentially makes the number of kerning
        # pairs involving groups a bit smaller).
        self.dissolve_single = False


class WhichApp(object):
    '''
    Test the environment.
    When running from the command line,
    'Defcon' is the expected environment
    '''

    def __init__(self):
        self.inRF = False
        self.inFL = False
        self.inDC = False
        self.appName = 'noApp'

        if not any((self.inRF, self.inFL, self.inDC)):
            try:
                import mojo.roboFont
                self.inRF = True
                self.appName = 'Robofont'
            except ImportError:
                pass

        if not any((self.inRF, self.inFL, self.inDC)):
            try:
                import flsys
                self.inFL = True
                self.appName = 'FontLab'
            except ImportError:
                pass

        if not any((self.inRF, self.inFL, self.inDC)):
            try:
                import defcon
                self.inDC = True
                self.appName = 'Defcon'
            except ImportError:
                pass


class FLKerningData(object):

    def __init__(self, font=None):
        self.f = font
        if font:
            self._readFLGroups()
            self._splitFLGroups()
            self.leftKeyGlyphs = self._filterKeyGlyphs(self.leftGroups)
            self.rightKeyGlyphs = self._filterKeyGlyphs(self.rightGroups)
            self._readFLKerning()

    def _isMMfont(self):
        '''Check if the FontLab font is a Multiple Master font.'''

        if self.f[0].layers_number > 1:
            return True
        else:
            return False

    def _readFLGroups(self):
        self.groupToKeyglyph = {}
        self.groups = {}
        self.group_order = []

        fl_class_names = [cname for cname in self.f.classes if cname[0] == '_']
        for cString in fl_class_names:

            FLclassName = cString.split(':')[0]
            # FL class name, e.g. _L_LC_LEFT
            OTgroupName = '@%s' % FLclassName[1:]
            # OT group name, e.g. @L_LC_LEFT
            markedGlyphList = cString.split(':')[1].split()
            cleanGlyphList = [gName.strip("'") for gName in markedGlyphList]
            # key glyph marker stripped out

            for gName in markedGlyphList:
                if gName[-1] == "'":  # finds keyglyph
                    keyGlyphName = gName.strip("'")
                    break
                else:
                    keyGlyphName = markedGlyphList[0]
                    print(
                        '\tWARNING: Kerning class %s has no explicit key '
                        'glyph.\n\tUsing first glyph found (%s).' % (
                            FLclassName, keyGlyphName)
                    )

            self.group_order.append(OTgroupName)
            self.groupToKeyglyph[OTgroupName] = keyGlyphName
            self.groups[OTgroupName] = cleanGlyphList

    def _splitFLGroups(self):
        '''
        Split FontLab kerning classes into left and right sides; based on
        the class name. If classes do not have an explicit side-flag, they
        are assigned to both left and right sides.
        '''

        leftTagsList = tags_left
        rightTagsList = tags_right

        self.leftGroups = []
        self.rightGroups = []

        for groupName in self.groups:
            if any([tag in groupName for tag in leftTagsList]):
                self.leftGroups.append(groupName)
            elif any([tag in groupName for tag in rightTagsList]):
                self.rightGroups.append(groupName)
            else:
                self.leftGroups.append(groupName)
                self.rightGroups.append(groupName)

    def _filterKeyGlyphs(self, groupList):
        '''
        Return a dictionary
        {keyGlyph: FLClassName}
        for a given list of group names.
        '''

        filteredKeyGlyphs = {}

        for groupName in groupList:
            keyGlyphName = self.groupToKeyglyph[groupName]
            filteredKeyGlyphs[keyGlyphName] = groupName

        return filteredKeyGlyphs

    def _readFLKerning(self):
        '''
        Read FontLab kerning and converts it into a UFO-style kerning dict.
        '''

        self.kerning = {}
        glyphs = self.f.glyphs

        for gIndexLeft, glyphLeft in enumerate(glyphs):
            gNameLeft = glyphLeft.name
            flKerningArray = glyphs[gIndexLeft].kerning

            for flKerningPair in flKerningArray:
                gIndexRight = flKerningPair.key
                gNameRight = glyphs[gIndexRight].name

                if self._isMMfont():
                    kernValue = '<%s>' % ' '.join(
                        map(str, flKerningPair.values))
                    # flKerningPair.values is an array
                    # holding kern values for each master
                else:
                    kernValue = int(flKerningPair.value)

                pair = (
                    self.leftKeyGlyphs.get(gNameLeft, gNameLeft),
                    self.rightKeyGlyphs.get(gNameRight, gNameRight))
                self.kerning[pair] = kernValue


class KernProcessor(object):
    def __init__(
        self,
        groups=None, kerning=None,
        option_dissolve=False
    ):

        # kerning dicts containing pair-value combinations
        self.glyph_glyph = {}
        self.glyph_glyph_exceptions = {}
        self.glyph_group = {}
        self.glyph_group_exceptions = {}
        self.group_glyph_exceptions = {}
        self.group_group = {}
        self.predefined_exceptions = {}

        self.rtl_glyph_glyph = {}
        self.rtl_glyph_glyph_exceptions = {}
        self.rtl_glyph_group = {}
        self.rtl_glyph_group_exceptions = {}
        self.rtl_group_glyph_exceptions = {}
        self.rtl_group_group = {}
        self.rtl_predefined_exceptions = {}

        self.pairs_unprocessed = []
        self.pairs_processed = []

        self.reference_groups = {
            group: glyph_list for (group, glyph_list) in groups.items() if
            not self._isKerningGroup(group)
        }

        sanitized_kerning = self.sanitize_kerning(groups, kerning)
        used_group_names = self._get_used_groups(sanitized_kerning)
        used_groups = {
            g_name: groups.get(g_name) for g_name in used_group_names
        }

        if used_groups and option_dissolve:
            dissolved_groups, dissolved_kerning = self._dissolveSingleGroups(
                used_groups, sanitized_kerning)
            self.groups = self._remap_groups(dissolved_groups)
            self.kerning = self._remap_kerning(dissolved_kerning)

        else:
            self.groups = self._remap_groups(used_groups)
            self.kerning = self._remap_kerning(sanitized_kerning)

        if used_groups:
            self.grouped_left = self._getAllGroupedGlyphs(side='left')
            self.grouped_right = self._getAllGroupedGlyphs(side='right')

        self._findExceptions()

        if self.kerning and len(self.kerning.keys()):
            self.group_order = sorted(
                [gr_name for gr_name in self.groups])
            self._sanityCheck()

    def sanitize_kerning(self, groups, kerning):
        '''
        Check kerning dict for pairs referencing items that do not exist
        in the groups dict.

        This solution is not ideal since there is another chance for producing
        an invalid kerning pair -- by referencing a glyph name which is not in
        the font. The font object is not present in this class, so a comparison
        would be difficult to achieve. This check is better than nothing for
        the moment, since crashing downstream is avoided.
        '''
        all_pairs = [pair for pair in kerning.keys()]
        all_kerned_items = set([item for pair in all_pairs for item in pair])
        all_kerned_groups = [
            item for item in all_kerned_items if self._isGroup(item)]

        bad_groups = set(all_kerned_groups) - set(groups.keys())
        sanitized_kerning = {
            pair: value for
            pair, value in kerning.items() if
            not set(pair).intersection(bad_groups)}

        bad_kerning = sorted([
            pair for pair in kerning.keys() if
            pair not in sanitized_kerning.keys()])

        for pair in bad_kerning:
            print(
                'pair {} {} references non-existent group'.format(*pair))

        return sanitized_kerning

    def _remap_group_name(self, group_name):
        '''
        Remap a single group name from public.kern style to @MMK style
        '''
        if 'public.kern1.' in group_name:
            stripped_name = group_name.replace('public.kern1.', '')
            if stripped_name.startswith('@MMK_L_'):
                # UFO2 files contain the @ in the XML, Defon reads it as
                # 'public.kernX.@MMK'
                return stripped_name
            else:
                # UFO3 files just contain the public.kern notation
                return group_name.replace('public.kern1.', '@MMK_L_')

        elif 'public.kern2.' in group_name:
            stripped_name = group_name.replace('public.kern2.', '')
            if stripped_name.startswith('@MMK_R_'):
                return stripped_name
            else:
                return group_name.replace('public.kern2.', '@MMK_R_')
        else:
            return group_name

    def _remap_group_order(self, group_list):
        '''
        Remap group order list
        '''
        remapped_group_order = [
            self._remap_group_name(g_name) for g_name in group_list
        ]
        return remapped_group_order

    def _remap_groups(self, groups):
        '''
        Remap groups dictionary to not contain public.kern prefixes.
        '''
        remapped_groups = {}
        for group_name, glyph_list in groups.items():
            remapped_group_name = self._remap_group_name(group_name)
            remapped_groups[remapped_group_name] = glyph_list

        return remapped_groups

    def _remap_kerning(self, kerning):
        '''
        Remap kerning dictionary to not contain public.kern prefixes.
        '''
        remapped_kerning = {}
        for (left, right), value in kerning.items():
            remapped_pair = (
                self._remap_group_name(left),
                self._remap_group_name(right))
            remapped_kerning[remapped_pair] = value

        return remapped_kerning

    def _isGroup(self, itemName):
        '''
        Return True if the first character of a kerning item is "@".
        '''

        if itemName[0] == '@':
            return True
        if itemName.split('.')[0] == 'public':
            return True
        return False

    def _isKerningGroup(self, groupName):
        '''
        Return True if the first group is a kerning group.
        '''

        if groupName.startswith('@MMK_'):
            return True
        if groupName.startswith('public.kern'):
            return True
        return False

    def _isRTL(self, pair):
        '''
        Check if a given pair is RTL, by looking for a RTL-specific group
        tag. Also use the hard-coded list of RTL glyphs.
        '''

        RTLGlyphs = self.reference_groups.get(group_RTL, [])
        RTLkerningTags = [tag_ara, tag_heb, tag_RTL]

        if set(pair) & set(RTLGlyphs):
            # Any item in the pair is in the RTL glyph reference group.
            # This will work for glyph-glyph pairs only.
            return True

        for tag in RTLkerningTags:
            # Group tags indicate presence of RTL item.
            # This will work for any pair including a RTL group.
            if any([tag in item for item in pair]):
                return True
        return False

    def _isRTLGroup(self, groupName):
        '''
        Check if a given group is a RTL group
        '''
        RTLkerningTags = [tag_ara, tag_heb, tag_RTL]

        for tag in RTLkerningTags:
            if any([tag in groupName]):
                return True
        return False

    def _get_used_groups(self, kerning):
        '''
        Return all groups which are actually used in kerning,
        by iterating through the kerning pairs.
        '''
        groupList = []
        for left, right in kerning.keys():
            if self._isGroup(left):
                groupList.append(left)
            if self._isGroup(right):
                groupList.append(right)
        return sorted(set(groupList))

    def _getAllGroupedGlyphs(self, groupFilterList=None, side=None):
        '''
        Return lists of glyphs used in groups on left or right side.
        This is used to calculate the subtable size for a given list
        of groups (groupFilterList) used within that subtable.
        '''
        grouped_left = []
        grouped_right = []

        if not groupFilterList:
            groupFilterList = self.groups.keys()

        for left, right in self.kerning.keys():
            if self._isGroup(left) and left in groupFilterList:
                grouped_left.extend(self.groups.get(left))
            if self._isGroup(right) and right in groupFilterList:
                grouped_right.extend(self.groups.get(right))

        if side == 'left':
            return sorted(set(grouped_left))
        elif side == 'right':
            return sorted(set(grouped_right))
        else:
            return sorted(set(grouped_left)), sorted(set(grouped_right))

    def _dissolveSingleGroups(self, groups, kerning):
        '''
        Find any groups with a single-item glyph list,
        (which are not RTL groups) which can be dissolved
        into single, or group-to-glyph/glyph-to-group pairs.
        The intention is avoiding an overload of the group-group subtable.
        '''
        singleGroups = dict(
            [(group_name, glyphs) for group_name, glyphs in groups.items() if(
                len(glyphs) == 1 and not self._isRTLGroup(group_name))])
        if singleGroups:
            dissolvedKerning = {}
            for (left, right), value in kerning.items():
                dissolvedLeft = singleGroups.get(left, [left])[0]
                dissolvedRight = singleGroups.get(right, [right])[0]
                dissolvedKerning[(dissolvedLeft, dissolvedRight)] = value

            remainingGroups = dict(
                [(gr_name, glyphs) for gr_name, glyphs in groups.items() if(
                    gr_name not in singleGroups)]
            )
            return remainingGroups, dissolvedKerning

        else:
            return groups, kerning

    def _sanityCheck(self):
        '''
        Check if the number of kerning pairs input
        equals the number of kerning entries output.
        '''
        num_pairs_total = len(self.kerning.keys())
        num_pairs_processed = len(self.pairs_processed)
        num_pairs_unprocessed = len(self.pairs_unprocessed)
        if num_pairs_total != num_pairs_processed + num_pairs_unprocessed:
            print('Something went wrong...')
            print('Kerning pairs provided: %s' % num_pairs_total)
            print('Kern entries generated: %s' % (
                num_pairs_processed + num_pairs_unprocessed))
            print('Pairs not processed: %s' % (
                num_pairs_total - (num_pairs_processed + num_pairs_unprocessed)))

    def _explode(self, leftGlyphList, rightGlyphList):
        '''
        Return a list of tuples, containing all possible combinations
        of elements in both input lists.
        '''

        return list(itertools.product(leftGlyphList, rightGlyphList))

    def _findExceptions(self):
        '''
        Process kerning to find which pairs are exceptions,
        and which are just normal pairs.
        '''

        for pair in list(self.kerning.keys())[::-1]:

            # Skip pairs in which the name of the left glyph contains
            # the ignore tag.
            if tag_ignore in pair[0]:
                del self.kerning[pair]
                continue

            # Looking for pre-defined exception pairs, and filtering them out.
            if any([tag_exception in item for item in pair]):
                self.predefined_exceptions[pair] = self.kerning[pair]
                del self.kerning[pair]

        glyph_2_glyph = sorted(
            [pair for pair in self.kerning.keys() if(
                not self._isGroup(pair[0]) and
                not self._isGroup(pair[1]))]
        )
        glyph_2_group = sorted(
            [pair for pair in self.kerning.keys() if(
                not self._isGroup(pair[0]) and
                self._isGroup(pair[1]))]
        )
        group_2_item = sorted(
            [pair for pair in self.kerning.keys() if(
                self._isGroup(pair[0]))]
        )

        # glyph to group pairs:
        # ---------------------
        for (glyph, group) in glyph_2_group:
            pair = (glyph, group)
            value = self.kerning[pair]
            groupList = self.groups[group]
            isRTLpair = self._isRTL(pair)
            if glyph in self.grouped_left:
                # it is a glyph_to_group exception!
                if isRTLpair:
                    self.rtl_glyph_group_exceptions[glyph, group] = value
                else:
                    self.glyph_group_exceptions[glyph, group] = value
                self.pairs_processed.append(pair)

            else:
                for groupedGlyph in groupList:
                    x_pair = (glyph, groupedGlyph)
                    if x_pair in glyph_2_glyph:
                        value = self.kerning[x_pair]
                        # that pair is a glyph_to_glyph exception!
                        if isRTLpair:
                            self.rtl_glyph_glyph_exceptions[x_pair] = value
                        else:
                            self.glyph_glyph_exceptions[x_pair] = value
                        # self.pairs_processed.append(pair)

                else:
                    # skip the pair if the value is zero
                    if value == 0:
                        self.pairs_unprocessed.append((glyph, group))
                        continue

                    if isRTLpair:
                        self.rtl_glyph_group[glyph, group] = value
                    else:
                        self.glyph_group[glyph, group] = value
                    self.pairs_processed.append((glyph, group))

        # group to group/glyph pairs:
        # ---------------------------
        explodedPairList = []
        RTLexplodedPairList = []

        for (leftGroup, rightItem) in group_2_item:
            # the right item of the pair may be a group or a glyph
            pair = (leftGroup, rightItem)
            value = self.kerning[pair]
            isRTLpair = self._isRTL(pair)
            l_group_glyphs = self.groups[leftGroup]

            if self._isGroup(rightItem):
                r_group_glyphs = self.groups[rightItem]
            else:
                # not a group, therefore a glyph
                if rightItem in self.grouped_right:
                    # it is a group_to_glyph exception!
                    if isRTLpair:
                        self.rtl_group_glyph_exceptions[pair] = value
                    else:
                        self.group_glyph_exceptions[pair] = value
                        self.pairs_processed.append(pair)
                    continue  # It is an exception, so move on to the next pair

                else:
                    r_group_glyphs = [rightItem]

            # skip the pair if the value is zero
            if value == 0:
                self.pairs_unprocessed.append(pair)
                continue

            if isRTLpair:
                self.rtl_group_group[pair] = value
                RTLexplodedPairList.extend(
                    self._explode(l_group_glyphs, r_group_glyphs))
            else:
                self.group_group[pair] = value
                explodedPairList.extend(
                    self._explode(l_group_glyphs, r_group_glyphs))
            self.pairs_processed.append(pair)

        # Find the intersection of the exploded pairs with the glyph_2_glyph
        # pairs collected above. Those must be exceptions, as they occur twice
        # (once in class-kerning, once as a single pair).
        self.exceptionPairs = set(explodedPairList) & set(glyph_2_glyph)
        self.RTLexceptionPairs = set(RTLexplodedPairList) & set(glyph_2_glyph)

        for pair in self.exceptionPairs:
            self.glyph_glyph_exceptions[pair] = self.kerning[pair]

        for pair in self.RTLexceptionPairs:
            self.rtl_glyph_glyph_exceptions[pair] = self.kerning[pair]

        # finally, collect normal glyph to glyph pairs:
        # ---------------------------------------------
        # NB: RTL glyph-to-glyph pairs can only be identified if its
        # glyphs are in the @RTL_KERNING group.

        for glyph_1, glyph_2 in glyph_2_glyph:
            pair = glyph_1, glyph_2
            value = self.kerning[pair]
            isRTLpair = self._isRTL(pair)
            if any(
                [glyph_1 in self.grouped_left, glyph_2 in self.grouped_right]
            ):
                # it is an exception!
                # exceptions expressed as glyph-to-glyph pairs -- these cannot
                # be filtered and need to be added to the kern feature
                # ---------------------------------------------
                if self._isRTL(pair):
                    self.rtl_glyph_glyph_exceptions[pair] = value
                else:
                    self.glyph_glyph_exceptions[pair] = value
                # self.pairs_processed.append(pair)
            else:
                if (
                    pair not in self.glyph_glyph_exceptions and
                    pair not in self.rtl_glyph_glyph_exceptions
                ):
                    if self._isRTL(pair):
                        self.rtl_glyph_glyph[pair] = self.kerning[pair]
                    else:
                        self.glyph_glyph[pair] = self.kerning[pair]
            self.pairs_processed.append(pair)


class MakeMeasuredSubtables(object):

    def __init__(self, kernDict, kerning, groups, maxSubtableSize):

        self.kernDict = kernDict
        self.subtables = []
        self.numberOfKernedGlyphs = self._getNumberOfKernedGlyphs(
            kerning, groups)

        coverageTableSize = 2 + (2 * self.numberOfKernedGlyphs)
        # maxSubtableSize = 2 ** 14

        print('coverage table size:', coverageTableSize)
        print('  max subtable size:', maxSubtableSize)
        # If Extension is not used, coverage and class subtables are
        # pushed to very end of GPOS block.
        #
        # Order is: script list, lookup list, feature list, then
        # table that contains lookups.

        # GPOS table size
        # All GPOS lookups need to be considered
        # Look up size of all GPOS lookups

        measuredSubtables = []
        leftItems = sorted(set([left for left, right in self.kernDict.keys()]))

        groupedGlyphsLeft = set([])
        groupedGlyphsRight = set([])
        usedGroupsLeft = set([])
        usedGroupsRight = set([])

        subtable = []

        for item in leftItems:
            itemPair = [
                pair for pair in self.kernDict.keys() if pair[0] == item]

            for left, right in itemPair:
                groupedGlyphsLeft.update(groups.get(left, [left]))
                groupedGlyphsRight.update(groups.get(right, [right]))
                usedGroupsLeft.add(left)
                usedGroupsRight.add(right)

                leftClassSize = 6 + (2 * len(groupedGlyphsLeft))
                rightClassSize = 6 + (2 * len(groupedGlyphsRight))
                subtableMetadataSize = (
                    coverageTableSize + leftClassSize + rightClassSize)
                subtable_size = (
                    16 + len(usedGroupsLeft) * len(usedGroupsRight) * 2)

            if subtableMetadataSize + subtable_size < maxSubtableSize:
                subtable.append(item)

            else:
                measuredSubtables.append(subtable)

                subtable = []
                subtable.append(item)
                groupedGlyphsLeft = set([])
                groupedGlyphsRight = set([])
                usedGroupsLeft = set([])
                usedGroupsRight = set([])

        # Last subtable:
        if len(subtable):
            measuredSubtables.append(subtable)

        for leftItemList in measuredSubtables:
            stDict = {}
            for leftItem in leftItemList:
                for pair in [
                    pair for pair in self.kernDict.keys() if
                    pair[0] == leftItem
                ]:
                    stDict[pair] = kerning.get(pair)
            self.subtables.append(stDict)

    def _getNumberOfKernedGlyphs(self, kerning, groups):
        leftList = []
        rightList = []
        for left, right in kerning.keys():
            leftList.extend(groups.get(left, [left]))
            rightList.extend(groups.get(right, [right]))

        # This previous approach counts every glyph only once,
        # which I think might be wrong:
        # Coverage table includes left side glyphs only.
        # Could measure only left side in order to get size of coverage table.
        allKernedGlyphs = set(leftList) | set(rightList)
        return len(allKernedGlyphs)
        # (Assume that a glyph must be counted twice when kerned
        # on both sides).
        # return len(set(leftList)) + len(set(rightList))

        # Read’s advice:
        # every time you get to 48 k add UseExtension keyword
        # mark is a gpos feature too.


class run(object):

    def __init__(self, font, args=None):

        if not args:
            args = Defaults()

        if args.write_timestamp:
            self.header = ['# Created: %s' % time.ctime()]
        else:
            self.header = []

        appTest = WhichApp()
        output_file = args.output_file

        self.inFL = appTest.inFL
        self.f = font
        self.folder = os.path.dirname(font.path)
        self.minKern = args.min_value
        self.write_subtables = args.write_subtables
        self.subtable_size = args.subtable_size
        self.write_trimmed_pairs = args.write_trimmed_pairs
        self.dissolve_single = args.dissolve_single
        self.trimmedPairs = 0

        if self.inFL:
            self.header.append('# PS Name: %s' % self.f.font_name)

            fl_K = FLKerningData(self.f)

            self.MM = fl_K._isMMfont()
            self.kerning = fl_K.kerning
            self.groups = fl_K.groups
            self.group_order = fl_K.group_order

            if self.MM:
                output_file = 'mm' + output_file
            else:
                self.header.append('# MM Inst: %s' % self.f.menu_name)

        else:
            self.header.append(
                '# PS Name: %s' % self.f.info.postscriptFontName)

            self.MM = False
            self.kerning = self.f.kerning
            self.groups = self.f.groups
            self.group_order = sorted(self.groups.keys())

        if not self.kerning:
            print('\tERROR: The font has no kerning!')
            return

        self.header.append('# MinKern: +/- %s inclusive' % self.minKern)
        self.header.append('# exported from %s' % appTest.appName)

        outputData = self._makeOutputData(args)
        if outputData:
            self.writeDataToFile(outputData, output_file)

    def _dict2pos(self, pairValueDict, minimum=0, enum=False, RTL=False):
        '''
        Turn a dictionary to a list of kerning pairs. In a single master font,
        the function can filter kerning pairs whose absolute value does not
        exceed a given threshold.
        '''

        data = []
        trimmed = 0
        for pair, value in pairValueDict.items():

            if RTL:
                if self.MM:
                    # kern value is stored in an array represented
                    # as a string, for instance: '<10 20 30 40>'

                    values = value[1:-1].split()
                    values = [
                        '<{0} 0 {0} 0>'.format(kernValue) for
                        kernValue in values]
                    valueString = '<%s>' % ' '.join(values)
                    # create an (experimental, but consequent)
                    # string like this:
                    # <<10 0 10 0> <20 0 20 0> <30 0 30 0> <40 0 40 0>>

                else:
                    kernValue = value
                    valueString = '<{0} 0 {0} 0>'.format(kernValue)

            else:
                kernValue = value
                valueString = value

            posLine = 'pos %s %s;' % (' '.join(pair), valueString)
            enumLine = 'enum %s' % posLine

            if self.MM:  # no filtering happening in MM.
                if enum:
                    data.append(enumLine)
                else:
                    data.append(posLine)

            else:
                if enum:
                    data.append(enumLine)
                else:
                    if abs(kernValue) < minimum:
                        if self.write_trimmed_pairs:
                            data.append('# %s' % posLine)
                        trimmed += 1
                    else:
                        data.append(posLine)

        self.trimmedPairs += trimmed
        data.sort()

        return '\n'.join(data)

    def _buildSubtableOutput(self, subtableList, comment, RTL=False):
        subtableOutput = []
        subtableBreak = '\nsubtable;'

        if sum([len(subtable.keys()) for subtable in subtableList]) > 0:
            subtableOutput.append(comment)

        for table in subtableList:
            if len(table):

                if RTL:
                    self.RTLsubtablesCreated += 1
                    if self.RTLsubtablesCreated > 1:
                        subtableOutput.append(subtableBreak)

                else:
                    self.subtablesCreated += 1
                    if self.subtablesCreated > 1:
                        subtableOutput.append(subtableBreak)

                subtableOutput.append(
                    self._dict2pos(table, self.minKern, RTL=RTL))
        print('%s subtables created' % self.subtablesCreated)
        return subtableOutput

    def _makeOutputData(self, args):
        # Build the output data.

        output = []
        kp = KernProcessor(
            self.groups,
            self.kerning,
            self.dissolve_single
        )

        # ---------------
        # list of groups:
        # ---------------
        for groupName in kp.group_order:
            glyphList = kp.groups[groupName]
            if not glyphList:
                print('\tWARNING: Kerning group %s has no glyphs.' % groupName)
                continue
            output.append('%s = [%s];' % (groupName, ' '.join(glyphList)))

        # ------------------
        # LTR kerning pairs:
        # ------------------
        LTRorder = [
            # container_dict, minKern, comment, enum
            (kp.predefined_exceptions, 0,
                '\n# pre-defined exceptions:', True),
            (kp.glyph_glyph, self.minKern,
                '\n# glyph, glyph:', False),
            (kp.glyph_glyph_exceptions, 0,
                '\n# glyph, glyph exceptions:', False),
            (kp.glyph_group_exceptions, 0,
                '\n# glyph, group exceptions:', True),
            (kp.group_glyph_exceptions, 0,
                '\n# group, glyph exceptions:', True),
        ]

        LTRorderExtension = [
            # in case no subtables are desired
            (kp.glyph_group, self.minKern, '\n# glyph, group:', False),
            (kp.group_group, self.minKern, '\n# group, group/glyph:', False),
        ]

        # ------------------
        # RTL kerning pairs:
        # ------------------
        RTLorder = [
            # container_dict, minKern, comment, enum
            (kp.rtl_predefined_exceptions, 0,
                '\n# RTL pre-defined exceptions:', True),
            (kp.rtl_glyph_glyph, self.minKern,
                '\n# RTL glyph, glyph:', False),
            (kp.rtl_glyph_glyph_exceptions, 0,
                '\n# RTL glyph, glyph exceptions:', False),
            (kp.rtl_glyph_group_exceptions, 0,
                '\n# RTL glyph, group exceptions:', True),
            (kp.rtl_group_glyph_exceptions, 0,
                '\n# RTL group, glyph exceptions:', True),
        ]

        RTLorderExtension = [
            # in case no subtables are desired
            (kp.rtl_glyph_group, self.minKern,
                '\n# RTL glyph, group:', False),
            (kp.rtl_group_group, self.minKern,
                '\n# RTL group, group/glyph:', False)
        ]

        if not self.write_subtables:
            LTRorder.extend(LTRorderExtension)
            RTLorder.extend(RTLorderExtension)

        for container_dict, minKern, comment, enum in LTRorder:
            if container_dict:
                output.append(comment)
                output.append(
                    self._dict2pos(container_dict, minKern, enum))

        if self.write_subtables:
            self.subtablesCreated = 0

            glyph_to_class_subtables = MakeMeasuredSubtables(
                kp.glyph_group, kp.kerning, kp.groups,
                self.subtable_size).subtables
            output.extend(self._buildSubtableOutput(
                glyph_to_class_subtables, '\n# glyph, group:'))

            class_to_class_subtables = MakeMeasuredSubtables(
                kp.group_group, kp.kerning, kp.groups,
                self.subtable_size).subtables
            output.extend(self._buildSubtableOutput(
                class_to_class_subtables,
                '\n# group, glyph and group, group:')
            )

        # Check if RTL pairs exist
        rtlPairsExist = False
        for container_dict, _, _, _ in RTLorderExtension + RTLorder:
            if container_dict.keys():
                rtlPairsExist = True
                break

        if rtlPairsExist:

            lookupRTLopen = (
                '\n\nlookup RTL_kerning {\n'
                'lookupflag RightToLeft IgnoreMarks;\n')
            lookupRTLclose = '\n\n} RTL_kerning;\n'

            output.append(lookupRTLopen)

            for container_dict, minKern, comment, enum in RTLorder:
                if container_dict:
                    output.append(comment)
                    output.append(
                        self._dict2pos(
                            container_dict, minKern, enum, RTL=True))

            if self.write_subtables:
                self.RTLsubtablesCreated = 0

                rtl_glyph_class_subtables = MakeMeasuredSubtables(
                    kp.rtl_glyph_group, kp.kerning, kp.groups,
                    self.subtable_size).subtables
                output.extend(self._buildSubtableOutput(
                    rtl_glyph_class_subtables,
                    '\n# RTL glyph, group:', RTL=True))

                rtl_class_class_subtables = MakeMeasuredSubtables(
                    kp.rtl_group_group, kp.kerning, kp.groups,
                    self.subtable_size).subtables
                output.extend(self._buildSubtableOutput(
                    rtl_class_class_subtables,
                    '\n# RTL group, glyph and group, group:', RTL=True))

            output.append(lookupRTLclose)

        return output

    def writeDataToFile(self, data, fileName):

        print('\tSaving %s file...' % fileName)

        if self.trimmedPairs > 0:
            print('\tTrimmed pairs: %s' % self.trimmedPairs)

        outputPath = os.path.join(self.folder, fileName)

        with open(outputPath, 'w') as outfile:
            outfile.write('\n'.join(self.header))
            outfile.write('\n\n')
            if data:
                outfile.write('\n'.join(data))
                outfile.write('\n')

        if not self.inFL:
            print('\tOutput file written to %s' % outputPath)


def get_args():

    defaults = Defaults()
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        'input_file',
        help='input font file')

    parser.add_argument(
        '-o', '--output_file',
        action='store',
        default=defaults.output_file,
        help='change the output file name')

    parser.add_argument(
        '-m', '--min_value',
        action='store',
        default=defaults.min_value,
        metavar='INT',
        type=int,
        help='minimum kerning value')

    parser.add_argument(
        '-s', '--write_subtables',
        action='store_true',
        default=defaults.write_subtables,
        help='write subtables')

    parser.add_argument(
        '--subtable_size',
        action='store',
        default=defaults.subtable_size,
        metavar='INT',
        type=int,
        help='specify max subtable size')

    parser.add_argument(
        '-t', '--write_trimmed_pairs',
        action='store_true',
        default=defaults.write_trimmed_pairs,
        help='write trimmed pairs to fea file (as comments)')

    parser.add_argument(
        '--write_timestamp',
        action='store_true',
        default=defaults.write_timestamp,
        help='write time stamp in header of fea file')

    parser.add_argument(
        '--dissolve_single',
        action='store_true',
        default=defaults.dissolve_single,
        help='dissolve single-element groups to glyph names')

    return parser.parse_args()


if __name__ == '__main__':

    args = get_args()
    f_path = os.path.normpath(args.input_file)
    import defcon
    if os.path.exists(f_path):

        f = defcon.Font(f_path)
        run(f, args)

    else:
        print(f_path, 'does not exist.')
