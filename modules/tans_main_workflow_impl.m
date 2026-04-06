function outputs = tans_main_workflow_impl(Subdir, configFile)
%TANS_MAIN_WORKFLOW_IMPL Run the full PFM-TANS targeting workflow.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
if nargin < 2 || isempty(configFile)
    error('Usage: tans_main_workflow(Subdir, configFile)');
end

Subdir = char(Subdir);
[~, Subject] = fileparts(Subdir);
tans_add_repo_paths();

cfg = tans_load_config(Subdir, configFile);
cfg = i_apply_defaults(cfg, Subdir);
i_validate_config(cfg);

addpath(genpath(cfg.paths.resourcesRoot));
Paths = {cfg.paths.simnibsRoot, cfg.paths.mscRoot, cfg.paths.tansRoot};
for i = 1:numel(Paths)
    addpath(genpath(Paths{i}));
end

preflight = tans_preflight_check(Subdir, cfg, 'Stage', 'preheadmodel', ...
    'ErrorIfMissing', true, 'Verbose', true);

targetRootDir = fullfile(Subdir, 'tans', cfg.target.name);
if ~exist(targetRootDir, 'dir')
    mkdir(targetRootDir);
end
tans_write_runtime_provenance(targetRootDir, cfg, 'Stage', 'tans_main_workflow', ...
    'Extra', struct('subject', Subject));

T1w = fullfile(Subdir, 'anat', 'T1w', cfg.headmodel.t1File);
T2w = fullfile(Subdir, 'anat', 'T2w', cfg.headmodel.t2File);
headMeshPath = fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], [Subject '.msh']);
skipHeadModel = cfg.headmodel.skipExistingHeadModel && ...
    ~cfg.headmodel.overwriteExistingHeadModel && ...
    exist(headMeshPath, 'file') == 2;
if ~skipHeadModel
    tans_headmodels_native(Subject, T1w, T2w, fullfile(Subdir, 'tans'), Paths, cfg.headmodel);
end

tans_preflight_check(Subdir, cfg, 'Stage', 'postheadmodel', ...
    'ErrorIfMissing', true, 'Verbose', true);

[PialSurfs, WhiteSurfs, MidthickSurfs, MedialWallMasks, SkinSurf, HeadMesh] = ...
    i_prepare_subject_surfaces(Subdir, Subject, cfg);

probMaps = ft_read_cifti_mod(cfg.inputs.probMapsFile);
TargetNetwork = probMaps;
TargetNetwork.data = probMaps.data(:, cfg.target.networkColumn);
CutOff = prctile(TargetNetwork.data, cfg.target.thresholdPercentile);
TargetNetwork.data(TargetNetwork.data < CutOff) = 0;
TargetNetwork.data(TargetNetwork.data ~= 0) = 1;

SearchSpace = ft_read_cifti_mod(cfg.paths.searchSpace);
VertexSurfaceArea = ft_read_cifti_mod(fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k', ...
    sprintf('%s.midthickness_va.32k_fs_LR.dscalar.nii', Subject)));
Sulc = ft_read_cifti_mod(fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', ...
    sprintf('%s.sulc.32k_fs_LR.dscalar.nii', Subject)));
BrainStructure = SearchSpace.brainstructure;
Sulc.data(BrainStructure == -1) = [];

tolerabilityModel = tans_build_tolerability_model(Subdir, Subject, SkinSurf, targetRootDir, cfg);

roiResult = tans_roi(TargetNetwork, MidthickSurfs, VertexSurfaceArea, Sulc, ...
    SearchSpace, targetRootDir, Paths, cfg.target);
candidateCount = numel(roiResult.candidates);
if candidateCount == 0
    error('No viable candidate target clusters were identified.');
end

Target = probMaps;
Target.data = probMaps.data(:, cfg.target.networkColumn);
if cfg.target.useAvoidance
    OffTarget = probMaps;
    OffTarget.data = probMaps.data(:, cfg.target.offTargetColumn);
else
    OffTarget = [];
end

CoilModel = fullfile(cfg.paths.simnibsRoot, cfg.simnibs.coilRelativePath);
DistanceToScalp = cfg.simnibs.distanceToScalpMM;
AngleResolution = cfg.simnibs.angleResolutionDegrees;

candidateResults = repmat(struct, candidateCount, 1);
for i = 1:candidateCount
    candidate = roiResult.candidates(i);
    candidateOutDir = candidate.outDir;
    candidateResults(i).CandidateRank = candidate.rank;
    candidateResults(i).CandidateLabel = candidate.label;
    candidateResults(i).ClusterSizeMM2 = candidate.cluster_size_mm2;
    candidateResults(i).OutDir = candidateOutDir;

    extra = candidate;
    if isfield(extra, 'patch_struct')
        extra = rmfield(extra, 'patch_struct');
    end
    tans_write_runtime_provenance(candidateOutDir, cfg, 'Stage', candidate.label, 'Extra', extra);

    try
        TargetNetworkPatch = ft_read_cifti_mod(candidate.patchFile);
        [SubSampledSearchGrid, ~] = tans_searchgrid(TargetNetworkPatch, PialSurfs, SkinSurf, ...
            cfg.search.gridSpacingMM, cfg.search.gridRadiusMM, candidateOutDir, Paths);

        tans_simnibs(SubSampledSearchGrid, HeadMesh, CoilModel, AngleResolution, DistanceToScalp, ...
            SkinSurf, MidthickSurfs, WhiteSurfs, PialSurfs, MedialWallMasks, ...
            cfg.simnibs.nThreads, candidateOutDir, Paths, cfg.simnibs);

        searchGridFile = fullfile(candidateOutDir, 'SearchGrid', 'SubSampledSearchGrid.shape.gii');
        optResult = tans_optimize(Subject, Target, OffTarget, cfg.optimize.percentileThresholds, ...
            searchGridFile, DistanceToScalp, SkinSurf, VertexSurfaceArea, MidthickSurfs, ...
            WhiteSurfs, PialSurfs, MedialWallMasks, HeadMesh, ...
            cfg.optimize.angleResolutionDegrees, cfg.optimize.uncertaintyMM, ...
            CoilModel, candidateOutDir, Paths, cfg.optimize);

        tolResult = tans_sample_tolerability(candidateOutDir, tolerabilityModel, optResult.CoilCenterVertex);

        brainsightTxtPath = i_export_optimal_trajectory(candidateOutDir, CoilModel, cfg);

        candidateResults(i).Status = 'ok';
        candidateResults(i).ErrorMessage = '';
        candidateResults(i).SearchGrid = searchGridFile;
        candidateResults(i).OptimizeXfmTxt = optResult.OptimizeXfmTxt;
        candidateResults(i).OptimizeBrainsightTxt = brainsightTxtPath;
        candidateResults(i).CoilCenterCoords = optResult.CoilCenterCoords;
        candidateResults(i).CoilCenterVertex = optResult.CoilCenterVertex;
        candidateResults(i).CoilOrientationCoords = optResult.CoilOrientationCoords;
        candidateResults(i).BestCenterOnTarget = optResult.BestCenterOnTarget;
        candidateResults(i).BestCenterPenalty = optResult.BestCenterPenalty;
        candidateResults(i).BestCenterPenalizedOnTarget = optResult.BestCenterPenalizedOnTarget;
        candidateResults(i).BestOrientationOnTarget = optResult.BestOrientationOnTarget;
        candidateResults(i).BestOrientationPenalty = optResult.BestOrientationPenalty;
        candidateResults(i).BestOrientationPenalizedOnTarget = optResult.BestOrientationPenalizedOnTarget;
        candidateResults(i).Tolerability = tolResult;
    catch ME
        candidateResults(i).Status = 'failed';
        candidateResults(i).ErrorMessage = ME.message;
        candidateResults(i).SearchGrid = '';
        candidateResults(i).OptimizeXfmTxt = '';
        candidateResults(i).OptimizeBrainsightTxt = '';
        candidateResults(i).Tolerability = struct;
        warning('Candidate %d failed: %s', candidate.rank, ME.message);
    end
end

i_write_candidate_summary(targetRootDir, candidateResults, cfg);

outputs = struct;
outputs.Subdir = Subdir;
outputs.Subject = Subject;
outputs.TargetDir = targetRootDir;
outputs.CandidateCount = candidateCount;
outputs.CandidateResults = candidateResults;
outputs.Preflight = preflight;
outputs.ROI = roiResult;
outputs.TolerabilityModel = tolerabilityModel;
end

function cfg = i_apply_defaults(cfg, Subdir)
if ~isfield(cfg, 'Subdir') || isempty(cfg.Subdir)
    cfg.Subdir = Subdir;
end
if ~isfield(cfg, 'headmodel'); cfg.headmodel = struct; end
if ~isfield(cfg.headmodel, 'skipExistingHeadModel') || isempty(cfg.headmodel.skipExistingHeadModel)
    cfg.headmodel.skipExistingHeadModel = true;
end
if ~isfield(cfg.headmodel, 'overwriteExistingHeadModel') || isempty(cfg.headmodel.overwriteExistingHeadModel)
    cfg.headmodel.overwriteExistingHeadModel = false;
end
if ~isfield(cfg, 'target'); cfg.target = struct; end
if ~isfield(cfg.target, 'useAvoidance') || isempty(cfg.target.useAvoidance)
    cfg.target.useAvoidance = true;
end
if ~isfield(cfg.target, 'maxCandidateTargets') || isempty(cfg.target.maxCandidateTargets)
    cfg.target.maxCandidateTargets = 1;
end
if ~isfield(cfg, 'tolerability'); cfg.tolerability = struct; end
end

function i_validate_config(cfg)
requiredTop = {'paths', 'inputs', 'headmodel', 'target', 'search', 'simnibs', 'optimize', 'tolerability'};
for i = 1:numel(requiredTop)
    assert(isfield(cfg, requiredTop{i}), 'Missing cfg.%s', requiredTop{i});
end

assert(cfg.target.networkColumn > 0, 'cfg.target.networkColumn must be > 0');
assert(cfg.target.thresholdPercentile > 0 && cfg.target.thresholdPercentile < 100, ...
    'cfg.target.thresholdPercentile must be between 0 and 100.');
assert(cfg.target.maxCandidateTargets >= 1 && mod(cfg.target.maxCandidateTargets, 1) == 0, ...
    'cfg.target.maxCandidateTargets must be an integer >= 1.');
assert(cfg.search.gridRadiusMM > 0, 'cfg.search.gridRadiusMM must be > 0');
assert(cfg.search.gridSpacingMM > 0, 'cfg.search.gridSpacingMM must be > 0');
assert(cfg.simnibs.nThreads >= 1, 'cfg.simnibs.nThreads must be >= 1');
assert(isfield(cfg.tolerability, 'dataFile') && ~isempty(cfg.tolerability.dataFile), ...
    'cfg.tolerability.dataFile is required.');
assert(isfield(cfg.tolerability, 'eegPositionsFile') && ~isempty(cfg.tolerability.eegPositionsFile), ...
    'cfg.tolerability.eegPositionsFile is required.');
end

function [PialSurfs, WhiteSurfs, MidthickSurfs, MedialWallMasks, SkinSurf, HeadMesh] = ...
    i_prepare_subject_surfaces(Subdir, Subject, cfg)
T1w_acpc = fullfile(Subdir, 'anat', 'T1w', 'T1w_acpc.nii.gz');
XFM = fullfile(Subdir, 'anat', 'T1w', 'xfms', 'acpc_inv.mat');
inSurfDir = fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k');
outSurfDir = fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k');
if ~exist(outSurfDir, 'dir')
    mkdir(outSurfDir);
end

for h = 1:numel(cfg.headmodel.hemispheres)
    hemi = cfg.headmodel.hemispheres{h};
    for s = 1:numel(cfg.headmodel.surfaceTypes)
        surfType = cfg.headmodel.surfaceTypes{s};
        inFn = sprintf('%s.%s.%s.32k_fs_LR.surf.gii', Subject, hemi, surfType);
        inPath = fullfile(inSurfDir, inFn);
        assert(exist(inPath, 'file') == 2, 'Missing surface: %s', inPath);

        outFn = sprintf('%s.%s.%s.native.32k_fs_LR.surf.gii', Subject, hemi, surfType);
        outPath = fullfile(outSurfDir, outFn);
        if ~(cfg.headmodel.skipExistingNativeSurfaces && exist(outPath, 'file') == 2)
            cmd = sprintf(['wb_command -surface-apply-affine %s %s %s ' ...
                '-flirt %s %s'], inPath, XFM, outPath, T1w_acpc, ...
                fullfile(Subdir, 'anat', 'T1w', cfg.headmodel.t1File));
            [status, out] = system(cmd);
            if status ~= 0
                error('wb_command failed for %s\n%s', inPath, out);
            end
            if strcmp(hemi, 'L')
                structure = 'CORTEX_LEFT';
            else
                structure = 'CORTEX_RIGHT';
            end
            system(sprintf('wb_command -set-structure %s %s', outPath, structure));
        end
    end
end

MedialWallMasks = {
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.L.atlasroi.32k_fs_LR.shape.gii', Subject)), ...
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.R.atlasroi.32k_fs_LR.shape.gii', Subject))};
PialSurfs = {
    fullfile(outSurfDir, sprintf('%s.L.pial.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(outSurfDir, sprintf('%s.R.pial.native.32k_fs_LR.surf.gii', Subject))};
WhiteSurfs = {
    fullfile(outSurfDir, sprintf('%s.L.white.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(outSurfDir, sprintf('%s.R.white.native.32k_fs_LR.surf.gii', Subject))};
MidthickSurfs = {
    fullfile(outSurfDir, sprintf('%s.L.midthickness.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(outSurfDir, sprintf('%s.R.midthickness.native.32k_fs_LR.surf.gii', Subject))};
SkinSurf = fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], 'Skin.surf.gii');
HeadMesh = fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], [Subject '.msh']);
end

function brainsightTxtPath = i_export_optimal_trajectory(candidateOutDir, CoilModel, cfg)
[~, coilName] = fileparts(CoilModel);
if isfield(cfg, 'export') && isfield(cfg.export, 'optimizeXfmFileName') && ~isempty(cfg.export.optimizeXfmFileName)
    optimizeXfmFileName = cfg.export.optimizeXfmFileName;
else
    optimizeXfmFileName = [coilName '_xfm.txt'];
end

optimizeXfmTxt = fullfile(candidateOutDir, 'Optimize', optimizeXfmFileName);
assert(exist(optimizeXfmTxt, 'file') == 2, 'Missing optimized transform file: %s', optimizeXfmTxt);
brainsightTxtPath = '';

if ~isfield(cfg, 'export') || ~isfield(cfg.export, 'writeBrainsightTxt') || cfg.export.writeBrainsightTxt
    brainsightName = 'OptimalTrajectoryBS.txt';
    if isfield(cfg.export, 'brainsightFileName') && ~isempty(cfg.export.brainsightFileName)
        brainsightName = cfg.export.brainsightFileName;
    end

    pythonExe = fullfile(cfg.paths.simnibsRoot, 'simnibs_env', 'bin', 'python3.11');
    if isfield(cfg.export, 'pythonExe') && ~isempty(cfg.export.pythonExe)
        pythonExe = cfg.export.pythonExe;
    end

    brainsightTxtPath = fullfile(candidateOutDir, 'Optimize', brainsightName);
    export_tms_to_brainsight_from_txt(optimizeXfmTxt, brainsightTxtPath, 'PythonExe', pythonExe);
end
end

function i_write_candidate_summary(targetRootDir, candidateResults, cfg)
summaryFile = fullfile(targetRootDir, 'CandidateSummary.tsv');
fid = fopen(summaryFile, 'w');
assert(fid > 0, 'Unable to open summary file: %s', summaryFile);
cleanupObj = onCleanup(@() fclose(fid));

tolerabilityMetricNames = i_collect_tolerability_metric_names(candidateResults, cfg);
primaryMetric = i_primary_metric_name(tolerabilityMetricNames, cfg);

header = ['candidate_rank\tcandidate_label\tstatus\tcluster_size_mm2\t' ...
    'best_center_on_target\tbest_center_penalty\tbest_center_penalized\t' ...
    'best_orientation_on_target\tbest_orientation_penalty\tbest_orientation_penalized\t'];
for i = 1:numel(tolerabilityMetricNames)
    header = [header sprintf('tolerability_%s\t', tolerabilityMetricNames{i})]; %#ok<AGROW>
end
header = [header 'tolerability_rank\tout_dir\terror_message\n'];
fprintf(fid, header);

ranks = i_rank_candidates(candidateResults, primaryMetric, cfg);

for i = 1:numel(candidateResults)
    r = candidateResults(i);
    row = sprintf('%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t', ...
        r.CandidateRank, ...
        r.CandidateLabel, ...
        i_get_field(r, 'Status', ''), ...
        i_num(r.ClusterSizeMM2), ...
        i_num(i_get_field(r, 'BestCenterOnTarget', NaN)), ...
        i_num(i_get_field(r, 'BestCenterPenalty', NaN)), ...
        i_num(i_get_field(r, 'BestCenterPenalizedOnTarget', NaN)), ...
        i_num(i_get_field(r, 'BestOrientationOnTarget', NaN)), ...
        i_num(i_get_field(r, 'BestOrientationPenalty', NaN)), ...
        i_num(i_get_field(r, 'BestOrientationPenalizedOnTarget', NaN)));
    fprintf(fid, '%s', row);
    for j = 1:numel(tolerabilityMetricNames)
        fprintf(fid, '%s\t', i_num(i_tolerability_value(r, tolerabilityMetricNames{j})));
    end
    fprintf(fid, '%s\t%s\t%s\n', i_num(ranks(i)), i_get_field(r, 'OutDir', ''), ...
        strrep(i_get_field(r, 'ErrorMessage', ''), newline, ' | '));
end

save(fullfile(targetRootDir, 'CandidateSummary.mat'), 'candidateResults');
end

function metricNames = i_collect_tolerability_metric_names(candidateResults, cfg)
metricNames = {};
if isfield(cfg, 'tolerability') && isfield(cfg.tolerability, 'metricColumns') && ~isempty(cfg.tolerability.metricColumns)
    metricNames = cellfun(@matlab.lang.makeValidName, cellstr(cfg.tolerability.metricColumns), 'UniformOutput', false);
end
for i = 1:numel(candidateResults)
    r = candidateResults(i);
    if isfield(r, 'Tolerability') && isfield(r.Tolerability, 'metrics') && ~isempty(fieldnames(r.Tolerability.metrics))
        metricNames = fieldnames(r.Tolerability.metrics);
        return;
    end
end
end

function primary = i_primary_metric_name(metricNames, cfg)
primary = '';
if isempty(metricNames)
    return;
end
if isfield(cfg.tolerability, 'primaryMetric') && ~isempty(cfg.tolerability.primaryMetric)
    primary = matlab.lang.makeValidName(cfg.tolerability.primaryMetric);
    return;
end
primary = metricNames{1};
end

function ranks = i_rank_candidates(candidateResults, primaryMetric, cfg)
ranks = nan(numel(candidateResults), 1);
if isempty(primaryMetric)
    return;
end
vals = nan(numel(candidateResults), 1);
okMask = false(numel(candidateResults), 1);
for i = 1:numel(candidateResults)
    vals(i) = i_tolerability_value(candidateResults(i), primaryMetric);
    okMask(i) = strcmp(i_get_field(candidateResults(i), 'Status', ''), 'ok') && isfinite(vals(i));
end
if ~any(okMask)
    return;
end
okIdx = find(okMask);
okVals = vals(okMask);
if isfield(cfg.tolerability, 'lowerIsBetter') && cfg.tolerability.lowerIsBetter
    [~, ord] = sort(okVals, 'ascend');
else
    [~, ord] = sort(okVals, 'descend');
end
ranks(okIdx(ord)) = 1:numel(okIdx);
end

function val = i_tolerability_value(result, metricName)
val = NaN;
if isfield(result, 'Tolerability') && isfield(result.Tolerability, 'metrics') && ...
        isfield(result.Tolerability.metrics, metricName)
    val = result.Tolerability.metrics.(metricName);
end
end

function out = i_num(x)
if isempty(x) || (isnumeric(x) && any(isnan(x)))
    out = 'NaN';
else
    out = num2str(x);
end
end

function out = i_get_field(S, fieldName, defaultVal)
if isfield(S, fieldName) && ~isempty(S.(fieldName))
    out = S.(fieldName);
else
    out = defaultVal;
end
end
