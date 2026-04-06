function report = tans_dose_preflight_check(Subdir, cfg, varargin)
%TANS_DOSE_PREFLIGHT_CHECK Validate required files for dose workflow.
%
% Goal
%   Verify dose-workflow inputs exist before running simulation and scoring.
%
% Usage
%   report = tans_dose_preflight_check(Subdir, cfg)
%   report = tans_dose_preflight_check(Subdir, cfg, 'ErrorIfMissing', tf, 'Verbose', tf)
%
% Inputs
%   Subdir (char/string)
%       Subject directory.
%   cfg (struct)
%       Dose workflow config struct used by `tans_dose_workflow`.
%   Name-Value
%       ErrorIfMissing (logical, default true)
%       Verbose (logical, default true)
%
% Outputs
%   report (struct)
%       `Subdir`, `Subject`, `TotalChecks`, `MissingCount`, `Missing`, `Passed`.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end

p = inputParser;
addParameter(p, 'ErrorIfMissing', true, @(x)islogical(x) && isscalar(x));
addParameter(p, 'Verbose', true, @(x)islogical(x) && isscalar(x));
parse(p, varargin{:});

errorIfMissing = p.Results.ErrorIfMissing;
verbose = p.Results.Verbose;

[~, Subject] = fileparts(Subdir);
checks = {};

% Required core files
checks(end+1, :) = {'Optimized magnE', cfg.inputs.optimizedMagnEFile}; %#ok<AGROW>
checks(end+1, :) = {'Functional networks', cfg.inputs.functionalNetworksFile}; %#ok<AGROW>

% Map source: either prob map OR both target/off-target maps.
hasTargetMapPath = ~isempty(cfg.inputs.targetMapFile);
hasOffTargetMapPath = ~isempty(cfg.inputs.offTargetMapFile);
useAvoidance = true;
if isfield(cfg, 'target') && isfield(cfg.target, 'useAvoidance') && ~isempty(cfg.target.useAvoidance)
    useAvoidance = cfg.target.useAvoidance;
end
useMapPair = hasTargetMapPath && (~useAvoidance || hasOffTargetMapPath);
if useMapPair
    checks(end+1, :) = {'Target map', cfg.inputs.targetMapFile}; %#ok<AGROW>
    if useAvoidance
        checks(end+1, :) = {'Off-target map', cfg.inputs.offTargetMapFile}; %#ok<AGROW>
    end
else
    checks(end+1, :) = {'Prob maps', cfg.inputs.probMapsFile}; %#ok<AGROW>
end

% Label/color source: prefer valid Priors file, else labels file.
hasPriors = ~isempty(cfg.inputs.networkPriorsFile) && exist(cfg.inputs.networkPriorsFile, 'file') == 2;
if hasPriors
    checks(end+1, :) = {'Network priors (colors)', cfg.inputs.networkPriorsFile}; %#ok<AGROW>
elseif ~isempty(cfg.inputs.networkLabelsFile)
    checks(end+1, :) = {'Network labels', cfg.inputs.networkLabelsFile}; %#ok<AGROW>
end

% rMT input defaults/fallbacks are accepted.
defaultRmtInputDir = fullfile(Subdir, 'tans', 'rMT');
if ~isempty(cfg.inputs.rmtBrainsightFile)
    checks(end+1, :) = {'rMT BrainSight trajectory', cfg.inputs.rmtBrainsightFile}; %#ok<AGROW>
else
    checks(end+1, :) = {'rMT BrainSight trajectory', fullfile(defaultRmtInputDir, 'rMT_Trajectory_BrainSight.txt')}; %#ok<AGROW>
end
if isempty(cfg.rmt.didtAperUs)
    if ~isempty(cfg.inputs.rmtDidtFile)
        checks(end+1, :) = {'rMT DiDt file', cfg.inputs.rmtDidtFile}; %#ok<AGROW>
    else
        checks(end+1, :) = {'rMT DiDt file', fullfile(defaultRmtInputDir, 'DiDt.txt')}; %#ok<AGROW>
    end
end

% Support files
checks(end+1, :) = {'Coil model', fullfile(cfg.paths.simnibsRoot, cfg.simnibs.coilRelativePath)}; %#ok<AGROW>
checks(end+1, :) = {'Vertex surface area', ...
    fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.midthickness_va.32k_fs_LR.dscalar.nii', Subject))}; %#ok<AGROW>
if ~isempty(cfg.inputs.gyralLabelsFile)
    checks(end+1, :) = {'Gyral labels', cfg.inputs.gyralLabelsFile}; %#ok<AGROW>
end
if ~isempty(cfg.inputs.priorsFile)
    checks(end+1, :) = {'rMT prior file', cfg.inputs.priorsFile}; %#ok<AGROW>
end

missing = {};
for i = 1:size(checks, 1)
    if exist(checks{i, 2}, 'file') ~= 2
        missing(end+1, :) = checks(i, :); %#ok<AGROW>
    end
end

% Additional semantic checks.
errors = {};
hasProbPath = ~isempty(cfg.inputs.probMapsFile);
if ~(hasProbPath || useMapPair)
    errors{end+1} = 'Provide cfg.inputs.probMapsFile OR cfg.inputs.targetMapFile (plus offTarget map when useAvoidance=true).'; %#ok<AGROW>
end
hasLabelSource = hasPriors || ...
    (~isempty(cfg.inputs.networkLabelsFile) && exist(cfg.inputs.networkLabelsFile, 'file') == 2);
if ~hasLabelSource
    errors{end+1} = 'Provide at least one valid label source: cfg.inputs.networkPriorsFile or cfg.inputs.networkLabelsFile.'; %#ok<AGROW>
end

report = struct;
report.Subdir = Subdir;
report.Subject = Subject;
report.TotalChecks = size(checks, 1);
report.MissingCount = size(missing, 1);
report.Missing = missing;
report.Errors = errors;
report.Passed = isempty(missing) && isempty(errors);

if verbose
    fprintf('[tans_dose_preflight_check] Checked %d required files. Missing: %d\n', ...
        report.TotalChecks, report.MissingCount);
    if ~isempty(missing)
        for i = 1:size(missing, 1)
            fprintf('  - %s: %s\n', missing{i, 1}, missing{i, 2});
        end
    end
    if ~isempty(errors)
        for i = 1:numel(errors)
            fprintf('  - Config: %s\n', errors{i});
        end
    end
end

if errorIfMissing && ~report.Passed
    error('Dose preflight failed. Missing %d files, %d config issues.', report.MissingCount, numel(errors));
end

end
