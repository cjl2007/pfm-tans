function outputs = tans_dose_workflow(Subdir, configFile)
%TANS_DOSE_WORKFLOW Run dose calibration + dose optimization workflow.
%
% Goal
%   Estimate an absolute activation threshold from an rMT-eliciting
%   BrainSight placement, then evaluate stimulation intensities for the
%   optimized network-targeting coil placement. Target/off-target maps may
%   be binary or probabilistic (configurable or auto-inferred downstream).
%
% Usage
%   outputs = tans_dose_workflow(Subdir, configFile)
%
% Inputs
%   Subdir (char/string)
%       Subject directory.
%   configFile (char/string/struct)
%       Dose workflow config function name, path to config `.m`, or struct.
%
% Outputs
%   outputs (struct)
%       Paths and scalar summaries for rMT simulation and dose outputs.
%
% Notes
%   If cfg.inputs.rmtBrainsightFile and/or cfg.inputs.rmtDidtFile are empty,
%   defaults are resolved from:
%   `<Subdir>/tans/rMT/rMT_Trajectory_BrainSight.txt` and
%   `<Subdir>/tans/rMT/DiDt.txt`.
%   For stimulated-network dlabel color schemes, this workflow prefers
%   cfg.inputs.networkPriorsFile (e.g., Priors.mat), then falls back to
%   cfg.inputs.networkLabelsFile.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
if nargin < 2 || isempty(configFile)
    error('Usage: tans_dose_workflow(Subdir, configFile)');
end

Subdir = char(Subdir);
[~, Subject] = fileparts(Subdir);
tans_add_repo_paths();
cfg = tans_load_config(Subdir, configFile);
cfg = i_apply_defaults(cfg);
cfg = i_autoresolve_input_paths(cfg, Subdir);
i_validate_config(cfg);
preflight = tans_dose_preflight_check(Subdir, cfg, 'ErrorIfMissing', true, 'Verbose', true);

addpath(genpath(cfg.paths.resourcesRoot));
Paths = {cfg.paths.simnibsRoot, cfg.paths.mscRoot, cfg.paths.tansRoot};
for i = 1:numel(Paths)
    addpath(genpath(Paths{i}));
end

TargetDir = fullfile(Subdir, 'tans', cfg.target.name);
if ~exist(TargetDir, 'dir'); mkdir(TargetDir); end

%% Shared subject paths
headModelDir = fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject]);
HeadMesh = fullfile(headModelDir, [Subject '.msh']);
CoilModel = fullfile(cfg.paths.simnibsRoot, cfg.simnibs.coilRelativePath);

MidthickSurfs = {
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.L.midthickness.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.R.midthickness.native.32k_fs_LR.surf.gii', Subject))};
WhiteSurfs = {
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.L.white.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.R.white.native.32k_fs_LR.surf.gii', Subject))};
PialSurfs = {
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.L.pial.native.32k_fs_LR.surf.gii', Subject)), ...
    fullfile(Subdir, 'tans', 'HeadModel', 'fsaverage_LR32k', sprintf('%s.R.pial.native.32k_fs_LR.surf.gii', Subject))};
MedialWallMasks = {
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.L.atlasroi.32k_fs_LR.shape.gii', Subject)), ...
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.R.atlasroi.32k_fs_LR.shape.gii', Subject))};

VertexSurfaceArea = ft_read_cifti_mod(fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k', ...
    sprintf('%s.midthickness_va.32k_fs_LR.dscalar.nii', Subject)));
mapMode = lower(char(string(cfg.target.mapMode)));
useProbMaps = strcmp(mapMode, 'probabilistic') || ...
    (isempty(cfg.inputs.targetMapFile) && isempty(cfg.inputs.offTargetMapFile));
useAvoidance = cfg.target.useAvoidance;

if useProbMaps
    probMaps = ft_read_cifti_mod(cfg.inputs.probMapsFile);
    TargetNetwork = probMaps;
    TargetNetwork.data = probMaps.data(:, cfg.target.networkColumn);
    if useAvoidance
        OffTarget = probMaps;
        OffTarget.data = probMaps.data(:, cfg.target.offTargetColumn);
    else
        OffTarget = [];
    end
else
    TargetNetwork = ft_read_cifti_mod(cfg.inputs.targetMapFile);
    if size(TargetNetwork.data,2) > 1
        TargetNetwork.data = TargetNetwork.data(:, cfg.target.networkColumn);
    else
        % Winner-take-all dlabel/dscalar map: binarize by network label value.
        TargetNetwork.data = double(TargetNetwork.data(:,1) == cfg.target.networkColumn);
    end
    if useAvoidance
        OffTarget = ft_read_cifti_mod(cfg.inputs.offTargetMapFile);
        if size(OffTarget.data,2) > 1
            OffTarget.data = OffTarget.data(:, cfg.target.offTargetColumn);
        else
            % Winner-take-all dlabel/dscalar map: binarize by network label value.
            OffTarget.data = double(OffTarget.data(:,1) == cfg.target.offTargetColumn);
        end
    else
        OffTarget = [];
    end
end
FunctionalNetworks = ft_read_cifti_mod(cfg.inputs.functionalNetworksFile);

%% rMT calibration simulation (shared across targets)
rmtDir = fullfile(Subdir, 'tans', 'rMT');
if ~exist(rmtDir, 'dir'); mkdir(rmtDir); end

defaultRmtInputDir = fullfile(Subdir, 'tans', 'rMT');
[rmtBrainsightFile, didtAperUs, didtSourceFile] = i_resolve_rmt_inputs(cfg, defaultRmtInputDir);
didtAperSec = didtAperUs * 1e6;

if exist(rmtBrainsightFile, 'file') == 2
    dstBrain = fullfile(rmtDir, 'rMT_Trajectory_BrainSight.txt');
    if ~strcmp(char(java.io.File(rmtBrainsightFile).getCanonicalPath()), char(java.io.File(dstBrain).getCanonicalPath()))
        copyfile(rmtBrainsightFile, dstBrain);
    end
end
if ~isempty(didtSourceFile) && exist(didtSourceFile, 'file') == 2
    dstDidt = fullfile(rmtDir, 'DiDt.txt');
    if ~strcmp(char(java.io.File(didtSourceFile).getCanonicalPath()), char(java.io.File(dstDidt).getCanonicalPath()))
        copyfile(didtSourceFile, dstDidt);
    end
end
writematrix(didtAperUs, fullfile(rmtDir, 'DiDt_A_per_us.txt'));
writematrix(didtAperSec, fullfile(rmtDir, 'DiDt_A_per_s.txt'));

rmtMagnEDscalar = fullfile(rmtDir, 'magnE.dscalar.nii');
if ~(cfg.rmt.skipExistingSimulation && exist(rmtMagnEDscalar, 'file') == 2)
    M = i_load_rmt_matsimnibs(rmtBrainsightFile, cfg, rmtDir);
    i_run_single_simulation(M, didtAperSec, HeadMesh, CoilModel, MidthickSurfs, WhiteSurfs, ...
        PialSurfs, MedialWallMasks, rmtDir);
end
E_rmt = ft_read_cifti_mod(rmtMagnEDscalar);

%% Define precentral ROI + optional prior-constrained sub-ROI
roiDir = fullfile(rmtDir, 'ROI');
if ~exist(roiDir, 'dir'); mkdir(roiDir); end

if isempty(cfg.inputs.gyralLabelsFile)
    gyralLabelsFile = fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', ...
        sprintf('%s.aparc.32k_fs_LR.dlabel.nii', Subject));
else
    gyralLabelsFile = cfg.inputs.gyralLabelsFile;
end
GyralLabels = ft_read_cifti_mod(gyralLabelsFile);
cortexN = nnz(E_rmt.brainstructure > 0 & E_rmt.brainstructure < 3);
IdxPrecentral = find(ismember(GyralLabels.data(1:cortexN, :), cfg.rmt.precentralLabelValue));

if isempty(cfg.inputs.priorsFile)
    IdxPrior = IdxPrecentral;
    priorMaskName = 'PrecentralOnly';
else
    PriorsRaw = load(cfg.inputs.priorsFile);
    PriorsSpatial = i_extract_spatial_prior_matrix(PriorsRaw, cfg.inputs.priorsFile);
    pcol = cfg.rmt.priorColumn;
    pvals = PriorsSpatial(:, pcol);
    IdxPrior = find(pvals > prctile(pvals, cfg.rmt.priorPercentile));
    IdxPrior = intersect(IdxPrior, IdxPrecentral);
    priorMaskName = 'PrecentralPlusPrior';
end
assert(~isempty(IdxPrior), 'No vertices in precentral/prior mask for threshold estimation.');

O = E_rmt; O.data = zeros(size(O.data,1),1); O.data(IdxPrecentral,1) = E_rmt.data(IdxPrecentral,1);
ft_write_cifti_mod(fullfile(roiDir, sprintf('RMT_%gAperUs_Precentral', didtAperUs)), O);
O = E_rmt; O.data = zeros(size(O.data,1),1); O.data(IdxPrior,1) = E_rmt.data(IdxPrior,1);
ft_write_cifti_mod(fullfile(roiDir, sprintf('RMT_%gAperUs_%s', didtAperUs, priorMaskName)), O);

%% Absolute threshold estimation
Eroi = E_rmt.data(IdxPrior, 1);
method = lower(strtrim(cfg.rmt.thresholdMethod));
switch method
    case 'percentile'
        AbsoluteThreshold = prctile(Eroi, cfg.rmt.thresholdQuantile * 100);
    case 'weighted_quantile'
        assert(exist('PriorsSpatial', 'var') == 1, ...
            'weighted_quantile requires cfg.inputs.priorsFile with `Spatial`.');
        p = PriorsSpatial(IdxPrior, cfg.rmt.priorColumn);
        p(p < 0) = 0;
        w = p .^ cfg.rmt.weightGamma;
        w = w ./ (sum(w) + eps);
        AbsoluteThreshold = i_weighted_quantile(Eroi, w, cfg.rmt.thresholdQuantile);
    otherwise
        error('Unsupported cfg.rmt.thresholdMethod: %s', cfg.rmt.thresholdMethod);
end
writematrix(AbsoluteThreshold, fullfile(rmtDir, 'AbsoluteThreshold_V_per_m.txt'));

%% Dose optimization at network-targeting optimized placement
doseDir = fullfile(TargetDir, cfg.dose.outDirName);
if ~exist(doseDir, 'dir'); mkdir(doseDir); end

didtRangeAperUs = cfg.dose.didtRangeAperUs(:)';
didtRangeAperSec = didtRangeAperUs * 1e6;
if ~isempty(cfg.inputs.networkPriorsFile) && exist(cfg.inputs.networkPriorsFile, 'file') == 2
    labelSource = cfg.inputs.networkPriorsFile;
else
    labelSource = cfg.inputs.networkLabelsFile;
end
doseOpts = struct;
doseOpts.mapMode = cfg.target.mapMode;
doseOpts.mapScalePercentile = cfg.target.mapScalePercentile;
doseOpts.selectionMetric = cfg.dose.selectionMetric;
doseOpts.selectionMode = cfg.dose.selectionMode;
doseOpts.selectionWeights = cfg.dose.selectionWeights;
doseOpts.diDtUnits = 'A/s';
doseOpts.referenceDiDtAperUs = cfg.dose.referenceDiDtAperUs;

tans_dose(cfg.inputs.optimizedMagnEFile, VertexSurfaceArea, didtRangeAperSec, ...
    AbsoluteThreshold, cfg.dose.minHotSpotSizeMM2, TargetNetwork, OffTarget, ...
    FunctionalNetworks, labelSource, doseDir, Paths, doseOpts);

outputs = struct;
outputs.Subdir = Subdir;
outputs.Subject = Subject;
outputs.TargetDir = TargetDir;
outputs.OutDir = doseDir;
outputs.RMTDir = rmtDir;
outputs.DoseDir = doseDir;
outputs.RMTDiDtAperUs = didtAperUs;
outputs.RMTDiDtSourceFile = didtSourceFile;
outputs.RMTBrainsightFile = rmtBrainsightFile;
outputs.AbsoluteThresholdVPerM = AbsoluteThreshold;
outputs.RMTMagnEDscalar = rmtMagnEDscalar;
outputs.OptimizedMagnEFile = cfg.inputs.optimizedMagnEFile;
outputs.DoseDiDtAperUs = didtRangeAperUs;
outputs.NetworkMapMode = cfg.target.mapMode;
outputs.NetworkLabelSource = labelSource;
outputs.MinDoseFile = fullfile(doseDir, 'MinDose.txt');
outputs.TargetDoseFile = fullfile(doseDir, 'TargetDose.txt');
outputs.BestDoseMagnEFile = fullfile(doseDir, 'magnE_BestCoilCenter+BestOrientation+BestDose.dtseries.nii');
outputs.Preflight = preflight;

end

function cfg = i_apply_defaults(cfg)
if ~isfield(cfg, 'inputs'); cfg.inputs = struct; end
if ~isfield(cfg.inputs, 'targetMapFile'); cfg.inputs.targetMapFile = ''; end
if ~isfield(cfg.inputs, 'offTargetMapFile'); cfg.inputs.offTargetMapFile = ''; end
if ~isfield(cfg.inputs, 'networkPriorsFile'); cfg.inputs.networkPriorsFile = ''; end
if ~isfield(cfg.inputs, 'networkLabelsFile'); cfg.inputs.networkLabelsFile = ''; end

if ~isfield(cfg, 'target'); cfg.target = struct; end
if ~isfield(cfg.target, 'mapMode') || isempty(cfg.target.mapMode)
    cfg.target.mapMode = 'auto';
end
if ~isfield(cfg.target, 'useAvoidance') || isempty(cfg.target.useAvoidance)
    cfg.target.useAvoidance = true;
end
if ~isfield(cfg.target, 'mapScalePercentile') || isempty(cfg.target.mapScalePercentile)
    cfg.target.mapScalePercentile = 99;
end
if ~isfield(cfg, 'dose'); cfg.dose = struct; end
if ~isfield(cfg.dose, 'selectionMetric') || isempty(cfg.dose.selectionMetric)
    cfg.dose.selectionMetric = 'on_target';
end
if ~isfield(cfg.dose, 'selectionMode') || isempty(cfg.dose.selectionMode)
    cfg.dose.selectionMode = 'single_metric';
end
if ~isfield(cfg.dose, 'selectionWeights') || isempty(cfg.dose.selectionWeights)
    cfg.dose.selectionWeights = [0.4 0.3 0.3];
end
if ~isfield(cfg.dose, 'referenceDiDtAperUs') || isempty(cfg.dose.referenceDiDtAperUs)
    cfg.dose.referenceDiDtAperUs = 1;
end
end

function cfg = i_autoresolve_input_paths(cfg, Subdir)
pfmDir = fullfile(Subdir, 'pfm');
if ~exist(pfmDir, 'dir')
    return;
end

if isempty(cfg.inputs.functionalNetworksFile) || exist(cfg.inputs.functionalNetworksFile, 'file') ~= 2
    candidates = { ...
        fullfile(pfmDir, 'RidgeFusion_VTX_Communities.dscalar.nii'), ...
        fullfile(pfmDir, 'RidgeFusion_VTX.dlabel.nii'), ...
        fullfile(pfmDir, 'MS-HBM_FunctionalNetworks_VertexWiseThresh0.01_w10_c10.dlabel.nii')};
    cfg.inputs.functionalNetworksFile = i_first_existing(candidates, cfg.inputs.functionalNetworksFile);
end

if isempty(cfg.inputs.networkLabelsFile) || exist(cfg.inputs.networkLabelsFile, 'file') ~= 2
    candidates = { ...
        fullfile(pfmDir, 'Bipartite_PhysicalCommunities+AlgorithmicLabeling_NetworkLabels.xls'), ...
        fullfile(pfmDir, 'NetworkLabels.xls')};
    cfg.inputs.networkLabelsFile = i_first_existing(candidates, cfg.inputs.networkLabelsFile);
end
end

function out = i_first_existing(candidates, defaultVal)
out = defaultVal;
for i = 1:numel(candidates)
    if exist(candidates{i}, 'file') == 2
        out = candidates{i};
        return;
    end
end
end

function cfg = i_load_config(Subdir, configFile)
if isstring(configFile)
    configFile = char(configFile);
end

if isstruct(configFile)
    cfg = configFile;
    return;
end

if ~ischar(configFile)
    error('configFile must be a config function name, path to .m file, or struct.');
end

[p, n, e] = fileparts(configFile);
if ~isempty(p) || ~isempty(e)
    if isempty(e)
        e = '.m';
    end
    cfgPath = fullfile(p, [n e]);
    assert(exist(cfgPath, 'file') == 2, 'Config file not found: %s', cfgPath);
    if ~isempty(p)
        addpath(p);
    end
    cfg = feval(n, Subdir);
else
    cfg = feval(configFile, Subdir);
end
end

function i_validate_config(cfg)
requiredTop = {'paths', 'inputs', 'target', 'simnibs', 'rmt', 'dose'};
for i = 1:numel(requiredTop)
    assert(isfield(cfg, requiredTop{i}), 'Missing cfg.%s', requiredTop{i});
end

assert(isfield(cfg.paths, 'resourcesRoot'), 'Missing cfg.paths.resourcesRoot');
assert(isfield(cfg.paths, 'mscRoot'), 'Missing cfg.paths.mscRoot');
assert(isfield(cfg.paths, 'simnibsRoot'), 'Missing cfg.paths.simnibsRoot');
assert(isfield(cfg.paths, 'tansRoot'), 'Missing cfg.paths.tansRoot');

assert(isfield(cfg.inputs, 'probMapsFile'), 'Missing cfg.inputs.probMapsFile');
assert(isfield(cfg.inputs, 'functionalNetworksFile'), 'Missing cfg.inputs.functionalNetworksFile');
assert(isfield(cfg.inputs, 'networkLabelsFile'), 'Missing cfg.inputs.networkLabelsFile');
assert(isfield(cfg.inputs, 'networkPriorsFile'), 'Missing cfg.inputs.networkPriorsFile');
assert(isfield(cfg.inputs, 'targetMapFile'), 'Missing cfg.inputs.targetMapFile');
assert(isfield(cfg.inputs, 'offTargetMapFile'), 'Missing cfg.inputs.offTargetMapFile');
assert(isfield(cfg.inputs, 'optimizedMagnEFile'), 'Missing cfg.inputs.optimizedMagnEFile');
assert(isfield(cfg.inputs, 'rmtBrainsightFile'), 'Missing cfg.inputs.rmtBrainsightFile');
assert(isfield(cfg.inputs, 'rmtDidtFile'), 'Missing cfg.inputs.rmtDidtFile');
assert(isfield(cfg.inputs, 'priorsFile'), 'Missing cfg.inputs.priorsFile');
assert(isfield(cfg.inputs, 'gyralLabelsFile'), 'Missing cfg.inputs.gyralLabelsFile');

assert(isfield(cfg.target, 'name'), 'Missing cfg.target.name');
assert(isfield(cfg.target, 'networkColumn'), 'Missing cfg.target.networkColumn');
assert(isfield(cfg.target, 'offTargetColumn'), 'Missing cfg.target.offTargetColumn');
assert(isfield(cfg.target, 'useAvoidance'), 'Missing cfg.target.useAvoidance');
assert(isfield(cfg.target, 'mapMode'), 'Missing cfg.target.mapMode');
assert(isfield(cfg.target, 'mapScalePercentile'), 'Missing cfg.target.mapScalePercentile');

assert(isfield(cfg.simnibs, 'coilRelativePath'), 'Missing cfg.simnibs.coilRelativePath');

assert(isfield(cfg.rmt, 'didtAperUs'), 'Missing cfg.rmt.didtAperUs');
assert(isfield(cfg.rmt, 'precentralLabelValue'), 'Missing cfg.rmt.precentralLabelValue');
assert(isfield(cfg.rmt, 'priorColumn'), 'Missing cfg.rmt.priorColumn');
assert(isfield(cfg.rmt, 'priorPercentile'), 'Missing cfg.rmt.priorPercentile');
assert(isfield(cfg.rmt, 'thresholdMethod'), 'Missing cfg.rmt.thresholdMethod');
assert(isfield(cfg.rmt, 'thresholdQuantile'), 'Missing cfg.rmt.thresholdQuantile');
assert(isfield(cfg.rmt, 'weightGamma'), 'Missing cfg.rmt.weightGamma');
assert(isfield(cfg.rmt, 'skipExistingSimulation'), 'Missing cfg.rmt.skipExistingSimulation');
assert(isfield(cfg.rmt, 'brainsightSampleIndex'), 'Missing cfg.rmt.brainsightSampleIndex');
assert(isfield(cfg.rmt, 'brainsightWhich'), 'Missing cfg.rmt.brainsightWhich');
assert(isfield(cfg.rmt, 'brainsightUseSimnibsPython'), 'Missing cfg.rmt.brainsightUseSimnibsPython');

assert(isfield(cfg.dose, 'didtRangeAperUs'), 'Missing cfg.dose.didtRangeAperUs');
assert(isfield(cfg.dose, 'minHotSpotSizeMM2'), 'Missing cfg.dose.minHotSpotSizeMM2');
assert(isfield(cfg.dose, 'outDirName'), 'Missing cfg.dose.outDirName');
assert(isfield(cfg.dose, 'selectionMetric'), 'Missing cfg.dose.selectionMetric');
assert(isfield(cfg.dose, 'selectionMode'), 'Missing cfg.dose.selectionMode');
assert(isfield(cfg.dose, 'selectionWeights'), 'Missing cfg.dose.selectionWeights');
assert(isfield(cfg.dose, 'referenceDiDtAperUs'), 'Missing cfg.dose.referenceDiDtAperUs');

hasProb = ~isempty(cfg.inputs.probMapsFile);
hasTargetMap = ~isempty(cfg.inputs.targetMapFile);
hasOffTargetMap = ~isempty(cfg.inputs.offTargetMapFile);
useAvoidance = cfg.target.useAvoidance;

% Accept either source mode:
% 1) one probabilistic map file with target (and optional off-target) columns, OR
% 2) explicit target map (and optional off-target map).
assert(hasProb || hasTargetMap, ...
    ['Dose workflow requires cfg.inputs.probMapsFile, or cfg.inputs.targetMapFile ', ...
     '(with cfg.inputs.offTargetMapFile also required when useAvoidance=true).']);

if hasProb
    assert(cfg.target.networkColumn > 0, 'cfg.target.networkColumn must be > 0 when using probMapsFile');
    if useAvoidance
        assert(cfg.target.offTargetColumn > 0, 'cfg.target.offTargetColumn must be > 0 when useAvoidance=true and using probMapsFile');
    end
elseif useAvoidance
    assert(hasOffTargetMap, 'cfg.inputs.offTargetMapFile is required when useAvoidance=true and using explicit targetMapFile.');
end
hasLabels = ~isempty(cfg.inputs.networkLabelsFile);
hasPriors = ~isempty(cfg.inputs.networkPriorsFile);
assert(~(isempty(cfg.inputs.networkLabelsFile) && isempty(cfg.inputs.networkPriorsFile)), ...
    'Provide cfg.inputs.networkPriorsFile or cfg.inputs.networkLabelsFile for dlabel colors.');
assert(hasLabels || hasPriors, 'Provide at least one label source path.');
mapMode = lower(char(string(cfg.target.mapMode)));
assert(ismember(mapMode, {'auto','binary','probabilistic'}), ...
    'cfg.target.mapMode must be auto, binary, or probabilistic.');
assert(islogical(cfg.target.useAvoidance) && isscalar(cfg.target.useAvoidance), ...
    'cfg.target.useAvoidance must be true/false.');
selectionMetric = lower(char(string(cfg.dose.selectionMetric)));
assert(ismember(selectionMetric, {'on_target','penalized'}), ...
    'cfg.dose.selectionMetric must be on_target or penalized.');
selectionMode = lower(char(string(cfg.dose.selectionMode)));
assert(ismember(selectionMode, {'single_metric','pareto'}), ...
    'cfg.dose.selectionMode must be single_metric or pareto.');
assert(isnumeric(cfg.dose.selectionWeights) && numel(cfg.dose.selectionWeights) == 3, ...
    'cfg.dose.selectionWeights must be [wOn wOff wHot].');
assert(isnumeric(cfg.dose.referenceDiDtAperUs) && isscalar(cfg.dose.referenceDiDtAperUs) && cfg.dose.referenceDiDtAperUs > 0, ...
    'cfg.dose.referenceDiDtAperUs must be a positive scalar.');
end

function M = i_load_rmt_matsimnibs(rmtBrainsightFile, cfg, rmtDir)
if exist('import_simnibs_matsimnibs_from_brainsight', 'file') ~= 2
    error(['Missing import_simnibs_matsimnibs_from_brainsight on MATLAB path. ', ...
        'Place it in PFM-TANS repo root or add its folder to MATLAB path.']);
end

outMatFile = fullfile(rmtDir, 'rmt_matsimnibs.mat');
[Ms, ~] = import_simnibs_matsimnibs_from_brainsight(rmtBrainsightFile, ...
    'Which', cfg.rmt.brainsightWhich, ...
    'UseSimnibsPython', cfg.rmt.brainsightUseSimnibsPython, ...
    'OutMatFile', outMatFile);

assert(ndims(Ms) == 3 && size(Ms,1) == 4 && size(Ms,2) == 4, ...
    'Unexpected matsimnibs array size from BrainSight import.');
idx = cfg.rmt.brainsightSampleIndex;
assert(idx >= 1 && idx <= size(Ms,3), ...
    'cfg.rmt.brainsightSampleIndex out of range (1..%d).', size(Ms,3));
M = Ms(:,:,idx);
writematrix(M, fullfile(rmtDir, 'rmt_matsimnibs_selected.txt'), 'Delimiter', 'tab');
end

function [brainsightFile, didtAperUs, didtSourceFile] = i_resolve_rmt_inputs(cfg, defaultRmtInputDir)
defaultBrainsight = fullfile(defaultRmtInputDir, 'rMT_Trajectory_BrainSight.txt');
defaultDidt = fullfile(defaultRmtInputDir, 'DiDt.txt');

brainsightFile = '';
if isfield(cfg.inputs, 'rmtBrainsightFile') && ~isempty(cfg.inputs.rmtBrainsightFile)
    brainsightFile = cfg.inputs.rmtBrainsightFile;
else
    brainsightFile = defaultBrainsight;
end
assert(exist(brainsightFile, 'file') == 2, ...
    'rMT BrainSight file not found. Provide cfg.inputs.rmtBrainsightFile or create: %s', defaultBrainsight);

didtAperUs = [];
didtSourceFile = '';
if isfield(cfg.rmt, 'didtAperUs') && ~isempty(cfg.rmt.didtAperUs)
    didtAperUs = cfg.rmt.didtAperUs;
end
if isempty(didtAperUs)
    if isfield(cfg.inputs, 'rmtDidtFile') && ~isempty(cfg.inputs.rmtDidtFile)
        didtSourceFile = cfg.inputs.rmtDidtFile;
    else
        didtSourceFile = defaultDidt;
    end
    assert(exist(didtSourceFile, 'file') == 2, ...
        'rMT DiDt file not found. Provide cfg.rmt.didtAperUs, cfg.inputs.rmtDidtFile, or create: %s', defaultDidt);
    txt = fileread(didtSourceFile);
    vals = sscanf(txt, '%f');
    assert(~isempty(vals), 'Unable to parse numeric DiDt from file: %s', didtSourceFile);
    didtAperUs = vals(1);
end
end

function i_run_single_simulation(M, didtAperSec, HeadMesh, CoilModel, MidthickSurfs, WhiteSurfs, PialSurfs, MedialWallMasks, outDir)
simDir = fullfile(outDir, 'Simulation');
if exist(simDir, 'dir')
    system(['rm -rf ' simDir]);
end

s = sim_struct('SESSION');
s.fnamehead = HeadMesh;
s.pathfem = [simDir '/'];
s.poslist{1} = sim_struct('TMSLIST');
s.poslist{1}.fnamecoil = CoilModel;
s.poslist{1}.pos(1).matsimnibs = M;
s.poslist{1}.pos(1).didt = didtAperSec;
s.map_to_vol = true;
s.fields = 'e';

run_simnibs(s);

[~, subj] = fileparts(HeadMesh);
system(['fslmerge -t ' simDir '/subject_volumes/magnE.nii.gz ' simDir '/subject_volumes/' subj '*_magnE.nii.gz']);
system(['wb_command -volume-to-surface-mapping ' simDir '/subject_volumes/magnE.nii.gz ' ...
    MidthickSurfs{1} ' ' simDir '/subject_volumes/magnE.L.32k_fs_LR.shape.gii -ribbon-constrained ' ...
    WhiteSurfs{1} ' ' PialSurfs{1} ' -interpolate ENCLOSING_VOXEL']);
system(['wb_command -volume-to-surface-mapping ' simDir '/subject_volumes/magnE.nii.gz ' ...
    MidthickSurfs{2} ' ' simDir '/subject_volumes/magnE.R.32k_fs_LR.shape.gii -ribbon-constrained ' ...
    WhiteSurfs{2} ' ' PialSurfs{2} ' -interpolate ENCLOSING_VOXEL']);
system(['wb_command -metric-mask ' simDir '/subject_volumes/magnE.L.32k_fs_LR.shape.gii ' ...
    MedialWallMasks{1} ' ' simDir '/subject_volumes/magnE.L.32k_fs_LR.shape.gii']);
system(['wb_command -metric-mask ' simDir '/subject_volumes/magnE.R.32k_fs_LR.shape.gii ' ...
    MedialWallMasks{2} ' ' simDir '/subject_volumes/magnE.R.32k_fs_LR.shape.gii']);
system(['wb_command -cifti-create-dense-scalar ' outDir '/magnE.dscalar.nii -left-metric ' ...
    simDir '/subject_volumes/magnE.L.32k_fs_LR.shape.gii -roi-left ' MedialWallMasks{1} ...
    ' -right-metric ' simDir '/subject_volumes/magnE.R.32k_fs_LR.shape.gii -roi-right ' MedialWallMasks{2}]);

system(['rm -rf ' simDir]);
end

function q = i_weighted_quantile(x, w, p)
x = x(:);
w = w(:);
mask = isfinite(x) & isfinite(w) & w > 0;
x = x(mask);
w = w(mask);
assert(~isempty(x), 'No valid values for weighted quantile.');
[x, ord] = sort(x);
w = w(ord);
c = cumsum(w) / sum(w);
i = find(c >= p, 1, 'first');
if isempty(i)
    q = x(end);
else
    q = x(i);
end
end

function Spatial = i_extract_spatial_prior_matrix(S, srcPath)
% Accept either:
% 1) top-level `Spatial`, or
% 2) top-level `Priors` struct with field `Spatial`.
if isfield(S, 'Spatial')
    Spatial = S.Spatial;
elseif isfield(S, 'Priors') && isstruct(S.Priors) && isfield(S.Priors, 'Spatial')
    Spatial = S.Priors.Spatial;
else
    error('Priors file missing `Spatial` (top-level or nested `Priors.Spatial`): %s', srcPath);
end
assert(isnumeric(Spatial) && ndims(Spatial) == 2, 'Invalid Spatial matrix in priors file: %s', srcPath);
end
