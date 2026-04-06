function outTable = tans_build_subject_eeg_coordinate_table(eegCsvFile, outFile)
%TANS_BUILD_SUBJECT_EEG_COORDINATE_TABLE Build SMART RefLocation coordinates from subject EEG positions.
%
% This maps SMART study labels onto subject-native EEG positions already
% projected onto the subject scalp by SimNIBS. Standard bilateral groups
% map directly to matching extended 10-20 labels. Nonstandard SMART labels
% are approximated with nearby extended 10-20 scalp positions:
%   Inion -> Iz
%   TPJ   -> TP7/TP8
%   LatIn -> I1/I2
%   LatOc -> PO9/PO10
%   V5    -> PO7/PO8
%   ATL   -> FT9/FT10

assert(exist(eegCsvFile, 'file') == 2, 'EEG positions file not found: %s', eegCsvFile);

rows = readcell(eegCsvFile, 'FileType', 'text', 'Delimiter', ',');
assert(size(rows, 2) >= 5, 'Unexpected EEG positions format: %s', eegCsvFile);

labels = string(rows(:, 5));
coords = nan(size(rows, 1), 3);
for i = 1:size(rows, 1)
    coords(i, :) = [str2double(string(rows{i, 2})), ...
        str2double(string(rows{i, 3})), ...
        str2double(string(rows{i, 4}))];
end

labelMap = {
    'FP',   {'Fp1', 'Fp2'}
    'AF',   {'AF3', 'AF4'}
    'F3.4', {'F3', 'F4'}
    'F7.8', {'F7', 'F8'}
    'FC1.2', {'FC1', 'FC2'}
    'FC5.6', {'FC5', 'FC6'}
    'C3.4', {'C3', 'C4'}
    'T7.8', {'T7', 'T8'}
    'CP1.2', {'CP1', 'CP2'}
    'CP5.6', {'CP5', 'CP6'}
    'P3.4', {'P3', 'P4'}
    'P7.8', {'P7', 'P8'}
    'PO3.4', {'PO3', 'PO4'}
    'O1.2', {'O1', 'O2'}
    'CZ',   {'Cz'}
    'FZ',   {'Fz'}
    'PZ',   {'Pz'}
    'OZ',   {'Oz'}
    'Inion', {'Iz'}
    'LatIn', {'I1', 'I2'}
    'LatOc', {'PO9', 'PO10'}
    'V5',   {'PO7', 'PO8'}
    'TPJ',  {'TP7', 'TP8'}
    'ATL',  {'FT9', 'FT10'}
    };

RefLocation = strings(0, 1);
site_label = strings(0, 1);
x = [];
y = [];
z = [];

for i = 1:size(labelMap, 1)
    refLabel = string(labelMap{i, 1});
    siteLabels = string(labelMap{i, 2});
    for j = 1:numel(siteLabels)
        idx = find(strcmpi(labels, siteLabels(j)), 1);
        assert(~isempty(idx), 'Missing EEG position label %s in %s', siteLabels(j), eegCsvFile);
        RefLocation(end+1, 1) = refLabel; %#ok<AGROW>
        site_label(end+1, 1) = siteLabels(j); %#ok<AGROW>
        x(end+1, 1) = coords(idx, 1); %#ok<AGROW>
        y(end+1, 1) = coords(idx, 2); %#ok<AGROW>
        z(end+1, 1) = coords(idx, 3); %#ok<AGROW>
    end
end

outTable = table(RefLocation, site_label, x, y, z);
if nargin >= 2 && ~isempty(outFile)
    writetable(outTable, outFile, 'FileType', 'text', 'Delimiter', '\t');
end
end
