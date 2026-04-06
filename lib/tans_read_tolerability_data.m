function data = tans_read_tolerability_data(cfg)
%TANS_READ_TOLERABILITY_DATA Read, aggregate, and validate tolerability data.

assert(isfield(cfg, 'dataFile') && ~isempty(cfg.dataFile), ...
    'cfg.tolerability.dataFile is required.');
assert(exist(cfg.dataFile, 'file') == 2, ...
    'Tolerability data file not found: %s', cfg.dataFile);

raw = i_read_delimited_cell(cfg.dataFile);
assert(size(raw, 1) >= 2, 'Tolerability data file is empty: %s', cfg.dataFile);

headers = i_cells_to_strings(raw(1, :));
headersLower = lower(strtrim(headers));
body = raw(2:end, :);

coordSpace = 'subject_native';

labelIdx = i_find_header(headersLower, cfg.labelColumn);
assert(~isempty(labelIdx), 'Missing tolerability label column: %s', cfg.labelColumn);
labels = i_cells_to_strings(body(:, labelIdx));

xIdx = i_find_header(headersLower, cfg.xColumn);
yIdx = i_find_header(headersLower, cfg.yColumn);
zIdx = i_find_header(headersLower, cfg.zColumn);
haveCoords = ~isempty(xIdx) && ~isempty(yIdx) && ~isempty(zIdx);

metricCols = cfg.metricColumns;
if isempty(metricCols)
    excluded = lower(string({cfg.labelColumn, cfg.xColumn, cfg.yColumn, cfg.zColumn}));
    metricCols = {};
    for i = 1:numel(headers)
        if ismember(headersLower(i), excluded)
            continue;
        end
        col = i_numeric_column(body(:, i));
        if all(isfinite(col))
            metricCols{end+1} = char(headers(i)); %#ok<AGROW>
        end
    end
end
assert(~isempty(metricCols), 'No tolerability metric columns were found.');

metricNames = cell(1, numel(metricCols));
metricMatrix = nan(size(body, 1), numel(metricCols));
for i = 1:numel(metricCols)
    colIdx = i_find_header(headersLower, metricCols{i});
    assert(~isempty(colIdx), 'Missing tolerability metric column: %s', metricCols{i});
    metricNames{i} = matlab.lang.makeValidName(char(headers(colIdx)));
    metricMatrix(:, i) = i_numeric_column(body(:, colIdx));
    assert(any(isfinite(metricMatrix(:, i))), ...
        'Metric column does not contain any numeric values: %s', metricCols{i});
end

[groupLabels, ~, groupIdx] = unique(strtrim(labels), 'stable');
groupCount = numel(groupLabels);
aggMetrics = struct;
for i = 1:numel(metricNames)
    vals = metricMatrix(:, i);
    aggVals = nan(groupCount, 1);
    for g = 1:groupCount
        groupVals = vals(groupIdx == g);
        groupVals = groupVals(isfinite(groupVals));
        if ~isempty(groupVals)
            aggVals(g) = mean(groupVals);
        end
    end
    missingGroups = groupLabels(~isfinite(aggVals));
    assert(isempty(missingGroups), ...
        'Metric %s is missing numeric values for grouped labels: %s', ...
        metricCols{i}, strjoin(cellstr(missingGroups(:)'), ', '));
    aggMetrics.(metricNames{i}) = aggVals;
end

if haveCoords
    coordsRaw = [i_numeric_column(body(:, xIdx)), i_numeric_column(body(:, yIdx)), i_numeric_column(body(:, zIdx))];
    aggCoords = nan(groupCount, 3);
    for g = 1:groupCount
        aggCoords(g, :) = mean(coordsRaw(groupIdx == g, :), 1);
    end
    sampleCoords = aggCoords;
    sampleSiteLabels = groupLabels;
    expandedMetrics = aggMetrics;
else
    assert(isfield(cfg, 'labelCoordinateFile') && ~isempty(cfg.labelCoordinateFile), ...
        ['A subject-native tolerability label-coordinate file is required when the ', ...
         'tolerability data file does not contain explicit coordinates.']);
    [sampleCoords, sampleSiteLabels, expandedMetrics] = i_expand_from_label_coordinates( ...
        groupLabels, aggMetrics, metricNames, cfg);
end

aggregatedTable = table(groupLabels, 'VariableNames', {cfg.labelColumn});
for i = 1:numel(metricNames)
    aggregatedTable.(metricNames{i}) = aggMetrics.(metricNames{i});
end

data = struct;
data.coordinateSpace = coordSpace;
data.groupLabels = groupLabels;
data.coordinates = sampleCoords;
data.siteLabels = sampleSiteLabels;
data.metrics = expandedMetrics;
data.metricNames = metricNames;
data.aggregatedTable = aggregatedTable;
end

function C = i_read_delimited_cell(filePath)
[~, ~, ext] = fileparts(filePath);
delimiter = '\t';
if strcmpi(ext, '.csv')
    delimiter = ',';
end
fid = fopen(filePath, 'r');
assert(fid > 0, 'Unable to open tolerability data file: %s', filePath);
cleanupObj = onCleanup(@() fclose(fid)); %#ok<NASGU>

rows = {};
maxCols = 0;
while true
    line = fgetl(fid);
    if ~ischar(line)
        break;
    end
    parts = strsplit(line, delimiter, 'CollapseDelimiters', false);
    parts = cellfun(@i_clean_token, parts, 'UniformOutput', false);
    rows{end+1, 1} = parts; %#ok<AGROW>
    maxCols = max(maxCols, numel(parts));
end

C = cell(numel(rows), maxCols);
for i = 1:numel(rows)
    parts = rows{i};
    C(i, 1:numel(parts)) = parts;
end
end

function vals = i_numeric_column(col)
vals = nan(size(col, 1), 1);
for i = 1:size(col, 1)
    v = col{i};
    if isnumeric(v)
        vals(i) = double(v);
    elseif islogical(v)
        vals(i) = double(v);
    elseif isstring(v) || ischar(v)
        vals(i) = str2double(string(v));
    end
end
end

function [coords, siteLabels, metrics] = i_expand_from_label_coordinates(groupLabels, aggMetrics, metricNames, cfg)
map = i_read_delimited_cell(cfg.labelCoordinateFile);
headers = lower(strtrim(i_cells_to_strings(map(1, :))));
body = map(2:end, :);

labelIdx = i_find_header(headers, cfg.labelColumn);
xIdx = i_find_header(headers, cfg.xColumn);
yIdx = i_find_header(headers, cfg.yColumn);
zIdx = i_find_header(headers, cfg.zColumn);
siteLabelIdx = i_find_header(headers, cfg.siteLabelColumn);

assert(~isempty(labelIdx) && ~isempty(xIdx) && ~isempty(yIdx) && ~isempty(zIdx), ...
    ['Label-coordinate file must contain columns matching cfg.tolerability.labelColumn, ', ...
     'xColumn, yColumn, and zColumn.']);

mapLabels = lower(strtrim(i_cells_to_strings(body(:, labelIdx))));
coords = [];
siteLabels = strings(0, 1);
metrics = struct;
for i = 1:numel(metricNames)
    metrics.(metricNames{i}) = [];
end

for i = 1:numel(groupLabels)
    key = lower(strtrim(groupLabels(i)));
    matchIdx = find(mapLabels == key);
    assert(~isempty(matchIdx), 'Unable to map tolerability label to coordinates: %s', groupLabels(i));
    thisCoords = [i_numeric_column(body(matchIdx, xIdx)), ...
        i_numeric_column(body(matchIdx, yIdx)), ...
        i_numeric_column(body(matchIdx, zIdx))];
    coords = [coords; thisCoords]; %#ok<AGROW>
    if ~isempty(siteLabelIdx)
        siteLabels = [siteLabels; i_cells_to_strings(body(matchIdx, siteLabelIdx))]; %#ok<AGROW>
    else
        siteLabels = [siteLabels; repmat(groupLabels(i), numel(matchIdx), 1)]; %#ok<AGROW>
    end
    for j = 1:numel(metricNames)
        val = aggMetrics.(metricNames{j})(i);
        metrics.(metricNames{j}) = [metrics.(metricNames{j}); repmat(val, numel(matchIdx), 1)]; %#ok<AGROW>
    end
end
end

function idx = i_find_header(headersLower, requested)
idx = [];
if isempty(requested)
    return;
end
requested = lower(string(requested));
requestedAlt = lower(string(matlab.lang.makeValidName(char(requested))));
idx = find(headersLower == requested | headersLower == requestedAlt, 1);
end

function token = i_clean_token(token)
if isstring(token)
    token = char(token);
end
token = strtrim(token);
if numel(token) >= 2 && token(1) == '"' && token(end) == '"'
    token = token(2:end-1);
end
end

function out = i_cells_to_strings(C)
out = strings(size(C));
for i = 1:numel(C)
    v = C{i};
    if (isobject(v) || isstring(v) || iscell(v)) && any(ismissing(v(:)))
        out(i) = "";
    elseif isempty(v)
        out(i) = "";
    elseif isstring(v)
        out(i) = v;
    elseif ischar(v)
        out(i) = string(v);
    elseif isnumeric(v) || islogical(v)
        out(i) = string(v);
    else
        out(i) = string(char(v));
    end
end
end
