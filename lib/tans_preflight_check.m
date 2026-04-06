function report = tans_preflight_check(Subdir, cfg, varargin)
%TANS_PREFLIGHT_CHECK Validate required files before running the workflow.
%
% Goal
%   Verify that required inputs, surfaces, masks, and coil files exist
%   at the requested workflow stage.
%
% Usage
%   report = tans_preflight_check(Subdir, cfg)
%   report = tans_preflight_check(Subdir, cfg, 'Stage', stageName, ...
%       'ErrorIfMissing', tf, 'Verbose', tf)
%
% Inputs
%   Subdir (char/string)
%       Subject directory. Defaults to `pwd` if omitted.
%   cfg (struct)
%       Workflow config struct used by `tans_main_workflow`.
%   Name-Value:
%       Stage (char/string, default 'preheadmodel')
%           `preheadmodel` checks only files that must exist before the
%           workflow can generate/reuse the subject head model. `postheadmodel`
%           also checks files produced under `tans/HeadModel/` that are
%           required for downstream tolerability/search steps.
%       ErrorIfMissing (logical, default true)
%           Throw an error when required files are missing.
%       Verbose (logical, default true)
%           Print a check summary and missing-file list.
%
% Outputs
%   report (struct)
%       `Subdir`, `Subject`, `TotalChecks`, `MissingCount`, `Missing`,
%       `Passed`.
%
% Side Effects
%   Optional console output and optional error throw. No file writes.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end

p = inputParser;
addParameter(p, 'Stage', 'preheadmodel', @(x)ischar(x) || (isstring(x) && isscalar(x)));
addParameter(p, 'ErrorIfMissing', true, @(x)islogical(x) && isscalar(x));
addParameter(p, 'Verbose', true, @(x)islogical(x) && isscalar(x));
parse(p, varargin{:});

stage = lower(char(string(p.Results.Stage)));
errorIfMissing = p.Results.ErrorIfMissing;
verbose = p.Results.Verbose;

[~, Subject] = fileparts(Subdir);
checks = {};

% Core required inputs and references
checks(end+1, :) = {'PFM prob maps', cfg.inputs.probMapsFile}; %#ok<AGROW>
checks(end+1, :) = {'Search space', cfg.paths.searchSpace}; %#ok<AGROW>
checks(end+1, :) = {'Tolerability data file', cfg.tolerability.dataFile}; %#ok<AGROW>

% Anatomy used by workflow
t1w = fullfile(Subdir, 'anat', 'T1w', cfg.headmodel.t1File);
checks(end+1, :) = {'T1w', t1w}; %#ok<AGROW>
if isfield(cfg.headmodel, 't2File') && ~isempty(cfg.headmodel.t2File)
    t2w = fullfile(Subdir, 'anat', 'T2w', cfg.headmodel.t2File);
    checks(end+1, :) = {'T2w', t2w}; %#ok<AGROW>
end

% Surface inputs required before native conversion
surfDir = fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k');
for h = 1:numel(cfg.headmodel.hemispheres)
    hemi = cfg.headmodel.hemispheres{h};
    for s = 1:numel(cfg.headmodel.surfaceTypes)
        surfType = cfg.headmodel.surfaceTypes{s};
        checks(end+1, :) = {sprintf('%s %s surface', hemi, surfType), ... %#ok<AGROW>
            fullfile(surfDir, sprintf('%s.%s.%s.32k_fs_LR.surf.gii', Subject, hemi, surfType))};
    end
end

% Additional files used by ROI/search/optimize
checks(end+1, :) = {'Vertex surface area', ...
    fullfile(Subdir, 'anat', 'T1w', 'fsaverage_LR32k', sprintf('%s.midthickness_va.32k_fs_LR.dscalar.nii', Subject))}; %#ok<AGROW>
checks(end+1, :) = {'Sulc', ...
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.sulc.32k_fs_LR.dscalar.nii', Subject))}; %#ok<AGROW>
checks(end+1, :) = {'L medial wall mask', ...
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.L.atlasroi.32k_fs_LR.shape.gii', Subject))}; %#ok<AGROW>
checks(end+1, :) = {'R medial wall mask', ...
    fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', sprintf('%s.R.atlasroi.32k_fs_LR.shape.gii', Subject))}; %#ok<AGROW>

coilModel = fullfile(cfg.paths.simnibsRoot, cfg.simnibs.coilRelativePath);
checks(end+1, :) = {'Coil model', coilModel}; %#ok<AGROW>
if isfield(cfg, 'export') && isfield(cfg.export, 'writeBrainsightTxt') && cfg.export.writeBrainsightTxt
    if isfield(cfg.export, 'pythonExe') && ~isempty(cfg.export.pythonExe)
        checks(end+1, :) = {'Brainsight export python', cfg.export.pythonExe}; %#ok<AGROW>
    end
end

switch stage
    case 'preheadmodel'
        % No additional stage-specific checks.
    case 'postheadmodel'
        checks(end+1, :) = {'Tolerability EEG positions file', cfg.tolerability.eegPositionsFile}; %#ok<AGROW>
        checks(end+1, :) = {'Head mesh', ...
            fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], [Subject '.msh'])}; %#ok<AGROW>
        checks(end+1, :) = {'Skin surface', ...
            fullfile(Subdir, 'tans', 'HeadModel', ['m2m_' Subject], 'Skin.surf.gii')}; %#ok<AGROW>
    otherwise
        error('Unsupported preflight stage: %s', stage);
end

missing = {};
for i = 1:size(checks, 1)
    if exist(checks{i, 2}, 'file') ~= 2
        missing(end+1, :) = checks(i, :); %#ok<AGROW>
    end
end

report = struct;
report.Subdir = Subdir;
report.Subject = Subject;
report.Stage = stage;
report.TotalChecks = size(checks, 1);
report.MissingCount = size(missing, 1);
report.Missing = missing;
report.Passed = isempty(missing);

if verbose
    fprintf('[tans_preflight_check] Stage=%s Checked %d required files. Missing: %d\n', ...
        report.Stage, report.TotalChecks, report.MissingCount);
    if ~report.Passed
        for i = 1:size(missing, 1)
            fprintf('  - %s: %s\n', missing{i, 1}, missing{i, 2});
        end
    end
end

if errorIfMissing && ~report.Passed
    error('Preflight failed. Missing %d required files.', report.MissingCount);
end

end
