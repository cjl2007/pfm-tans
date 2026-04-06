function model = tans_build_tolerability_model(Subdir, Subject, SkinSurfFile, targetRootDir, cfg)
%TANS_BUILD_TOLERABILITY_MODEL Build a dense scalp tolerability map.

assert(isfield(cfg, 'tolerability'), 'Missing cfg.tolerability section.');
tcfg = i_apply_defaults(cfg.tolerability);
if isfield(cfg, 'paths') && isfield(cfg.paths, 'mscRoot') && ~isempty(cfg.paths.mscRoot)
    addpath(genpath(cfg.paths.mscRoot));
end
assert(isfield(tcfg, 'eegPositionsFile') && ~isempty(tcfg.eegPositionsFile), ...
    'cfg.tolerability.eegPositionsFile is required.');

runTolDir = fullfile(targetRootDir, 'Tolerability');
if ~exist(runTolDir, 'dir')
    mkdir(runTolDir);
end

skinNative = gifti(SkinSurfFile);
vertexCoordsNative = double(skinNative.vertices);
evalCoordinateSpace = 'subject_native';
vertexCoords = vertexCoordsNative;
subjectCoordinateFile = fullfile(runTolDir, 'TolerabilitySourceSites_SubjectEEG.tsv');
tans_build_subject_eeg_coordinate_table(tcfg.eegPositionsFile, subjectCoordinateFile);
tcfg.labelCoordinateFile = subjectCoordinateFile;
data = tans_read_tolerability_data(tcfg);
sampleCoordsEval = data.coordinates;

metricMap = struct;
metricNames = data.metricNames;
nearestSourceDistanceMM = i_nearest_source_distance(vertexCoords, sampleCoordsEval);
maxExtrapolationDistanceMM = i_extrapolation_limit(sampleCoordsEval, tcfg);
validDomainMask = nearestSourceDistanceMM <= maxExtrapolationDistanceMM;
for i = 1:numel(metricNames)
    metricName = metricNames{i};
    vals = i_interpolate_values(vertexCoords, sampleCoordsEval, data.metrics.(metricName), tcfg);
    vals(~validDomainMask) = NaN;
    metricMap.(metricName) = vals;
end

for i = 1:numel(metricNames)
    metricName = metricNames{i};
    G = gifti;
    G.cdata = single(metricMap.(metricName));
    save(G, fullfile(runTolDir, sprintf('Tolerability_%s.shape.gii', metricName)));
end
G = gifti;
G.cdata = single(validDomainMask);
save(G, fullfile(runTolDir, 'Tolerability_ValidDomain.shape.gii'));

metadata = struct;
metadata.coordinate_space = data.coordinateSpace;
metadata.evaluation_coordinate_space = evalCoordinateSpace;
metadata.interpolation_method = tcfg.interpolationMethod;
metadata.extrapolation_limit_mode = tcfg.extrapolationLimitMode;
metadata.max_extrapolation_distance_mm = maxExtrapolationDistanceMM;
metadata.primary_metric = tcfg.primaryMetric;
metadata.lower_is_better = tcfg.lowerIsBetter;
metadata.n_source_sites = size(data.coordinates, 1);
metadata.n_group_labels = numel(data.groupLabels);
metadata.metric_names = metricNames;
metadata.source_data_file = tcfg.dataFile;
metadata.eeg_positions_file = tcfg.eegPositionsFile;
metadata.label_coordinate_file = subjectCoordinateFile;
metadata.site_grouping_column = tcfg.labelColumn;
metadata.site_labels = data.siteLabels;
metadata.transformed_site_coordinates_native_mm = sampleCoordsEval;
metadata.valid_domain_vertex_count = sum(validDomainMask);
metadata.nearest_source_distance_mm_summary = struct( ...
    'min', min(nearestSourceDistanceMM), ...
    'mean', mean(nearestSourceDistanceMM(validDomainMask)), ...
    'max_within_domain', max(nearestSourceDistanceMM(validDomainMask)));

tans_write_struct_txt(fullfile(runTolDir, 'TolerabilityModel.txt'), metadata);
save(fullfile(runTolDir, 'TolerabilityModel.mat'), 'metadata', 'data', 'metricMap', ...
    'vertexCoords', 'sampleCoordsEval', 'validDomainMask', 'nearestSourceDistanceMM');
if isfield(data, 'aggregatedTable') && istable(data.aggregatedTable)
    writetable(data.aggregatedTable, fullfile(runTolDir, 'AggregatedTolerabilitySites.tsv'), ...
        'FileType', 'text', 'Delimiter', '\t');
end

model = struct;
model.outDir = runTolDir;
model.vertexCoords = vertexCoords;
model.vertexCoordsNative = vertexCoordsNative;
model.metricMap = metricMap;
model.metricNames = metricNames;
model.primaryMetric = tcfg.primaryMetric;
model.lowerIsBetter = tcfg.lowerIsBetter;
model.evalCoordinateSpace = evalCoordinateSpace;
model.sampleCoordsEval = sampleCoordsEval;
model.validDomainMask = validDomainMask;
model.nearestSourceDistanceMM = nearestSourceDistanceMM;
model.maxExtrapolationDistanceMM = maxExtrapolationDistanceMM;
model.data = data;
model.cfg = tcfg;
end

function cfg = i_apply_defaults(cfg)
if ~isfield(cfg, 'coordinateSpace') || isempty(cfg.coordinateSpace)
    cfg.coordinateSpace = 'subject_native';
end
if ~isfield(cfg, 'labelColumn') || isempty(cfg.labelColumn)
    cfg.labelColumn = 'label';
end
if ~isfield(cfg, 'xColumn') || isempty(cfg.xColumn)
    cfg.xColumn = 'x';
end
if ~isfield(cfg, 'yColumn') || isempty(cfg.yColumn)
    cfg.yColumn = 'y';
end
if ~isfield(cfg, 'zColumn') || isempty(cfg.zColumn)
    cfg.zColumn = 'z';
end
if ~isfield(cfg, 'metricColumns') || isempty(cfg.metricColumns)
    cfg.metricColumns = {};
end
cfg.labelCoordinateFile = '';
if ~isfield(cfg, 'siteLabelColumn') || isempty(cfg.siteLabelColumn)
    cfg.siteLabelColumn = 'site_label';
end
if ~isfield(cfg, 'interpolationMethod') || isempty(cfg.interpolationMethod)
    cfg.interpolationMethod = 'idw';
end
if ~isfield(cfg, 'idwPower') || isempty(cfg.idwPower)
    cfg.idwPower = 2;
end
if ~isfield(cfg, 'nNeighbors') || isempty(cfg.nNeighbors)
    cfg.nNeighbors = 6;
end
if ~isfield(cfg, 'extrapolationLimitMode') || isempty(cfg.extrapolationLimitMode)
    cfg.extrapolationLimitMode = 'mean_nearest_neighbor';
end
if ~isfield(cfg, 'maxExtrapolationDistanceMM')
    cfg.maxExtrapolationDistanceMM = [];
end
if ~isfield(cfg, 'primaryMetric') || isempty(cfg.primaryMetric)
    cfg.primaryMetric = '';
end
if ~isfield(cfg, 'eegPositionsFile')
    cfg.eegPositionsFile = '';
end
if ~isfield(cfg, 'lowerIsBetter') || isempty(cfg.lowerIsBetter)
    cfg.lowerIsBetter = true;
end
end

function nearestD = i_nearest_source_distance(vertexCoords, sampleCoords)
D = pdist2(vertexCoords, sampleCoords);
nearestD = min(D, [], 2);
end

function maxDist = i_extrapolation_limit(sampleCoords, cfg)
if ~isempty(cfg.maxExtrapolationDistanceMM)
    maxDist = cfg.maxExtrapolationDistanceMM;
    return;
end

mode = lower(char(string(cfg.extrapolationLimitMode)));
switch mode
    case {'none', 'off', 'infinite'}
        maxDist = inf;
    case 'mean_nearest_neighbor'
        if size(sampleCoords, 1) < 2
            maxDist = inf;
            return;
        end
        D = pdist2(sampleCoords, sampleCoords);
        D(D == 0) = inf;
        maxDist = mean(min(D, [], 2));
    otherwise
        error('Unsupported tolerability extrapolation limit mode: %s', cfg.extrapolationLimitMode);
end
end

function outVals = i_interpolate_values(vertexCoords, sampleCoords, sampleVals, cfg)
method = lower(char(string(cfg.interpolationMethod)));
assert(ismember(method, {'idw', 'nearest'}), ...
    'Unsupported tolerability interpolation method: %s', cfg.interpolationMethod);

D = pdist2(vertexCoords, sampleCoords);
if strcmp(method, 'nearest')
    [~, idx] = min(D, [], 2);
    outVals = sampleVals(idx);
    return;
end

nNeighbors = min(cfg.nNeighbors, size(sampleCoords, 1));
[sortedD, sortedIdx] = sort(D, 2, 'ascend');
sortedD = sortedD(:, 1:nNeighbors);
sortedIdx = sortedIdx(:, 1:nNeighbors);

outVals = zeros(size(vertexCoords, 1), 1);
for i = 1:size(vertexCoords, 1)
    d = sortedD(i, :);
    idx = sortedIdx(i, :);
    if d(1) == 0
        exactMask = d == 0;
        outVals(i) = mean(sampleVals(idx(exactMask)));
        continue;
    end
    w = 1 ./ (d .^ cfg.idwPower);
    outVals(i) = sum(w .* sampleVals(idx)') / sum(w);
end
end
