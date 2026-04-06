function cfg = tans_dose_config_bd2_salience(Subdir)
% Study-specific defaults for BD2 salience dose workflow.
% Used by: tans_dose_workflow(Subdir, configFile)

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end
[~, Subject] = fileparts(Subdir);
repoDir = tans_add_repo_paths();

cfg = struct;
cfg.Subdir = Subdir;

% Tool/resource roots
cfg.paths.resourcesRoot = fullfile(repoDir, 'res0urces');
cfg.paths.mscRoot = '/path/to/msc';
cfg.paths.simnibsRoot = '/path/to/SimNIBS';
cfg.paths.tansRoot = repoDir;

% Input files
cfg.inputs.probMapsFile = fullfile(Subdir, 'func','rest','PFM', 'RidgeFusion_VTX_ProbMaps.dtseries.nii');
cfg.inputs.targetMapFile = '';    % optional winner-take-all target map
cfg.inputs.offTargetMapFile = ''; % optional winner-take-all off-target map
cfg.inputs.functionalNetworksFile = fullfile(Subdir,'func','rest','PFM', 'RidgeFusion_VTX.dlabel.nii');
cfg.inputs.networkLabelsFile = fullfile(Subdir, 'pfm', ...
    'Bipartite_PhysicalCommunities+AlgorithmicLabeling_NetworkLabels.xls');
cfg.inputs.networkPriorsFile = fullfile(cfg.paths.tansRoot, 'res0urces', 'Priors.mat');
cfg.inputs.optimizedMagnEFile = fullfile(Subdir, 'tans', 'Salience-LH', 'Optimize', ...
    'magnE_BestCoilCenter+BestOrientation.dtseries.nii');
cfg.inputs.rmtBrainsightFile = ''; % default: <Subdir>/tans/rMT/rMT_Trajectory_BrainSight.txt
cfg.inputs.rmtDidtFile = ''; % optional file with dI/dt in A/us
cfg.inputs.priorsFile = fullfile(cfg.paths.tansRoot, 'res0urces', 'Priors.mat');
cfg.inputs.gyralLabelsFile = fullfile(Subdir, 'anat', 'MNINonLinear', 'fsaverage_LR32k', ...
    sprintf('%s.aparc.32k_fs_LR.dlabel.nii', Subject));

% Target definition
cfg.target.name = 'Salience-LH';
cfg.target.networkColumn = 13;
cfg.target.offTargetColumn = 14;
cfg.target.useAvoidance = true;
cfg.target.mapMode = 'probabilistic';
cfg.target.mapScalePercentile = 99;

% SimNIBS
cfg.simnibs.coilRelativePath = fullfile('simnibs_env', 'lib', 'python3.11', 'site-packages', ...
    'simnibs', 'resources', 'coil_models', 'Drakaki_BrainStim_2022', 'MagVenture_Cool-B65.ccd');

% rMT calibration
cfg.rmt.didtAperUs = 93; % set [] to read from cfg.inputs.rmtDidtFile/default DiDt.txt
cfg.rmt.precentralLabelValue = 24;
cfg.rmt.priorColumn = 16;
cfg.rmt.priorPercentile = 90;
cfg.rmt.thresholdMethod = 'weighted_quantile';
cfg.rmt.thresholdQuantile = 0.99;
cfg.rmt.weightGamma = 2;
cfg.rmt.skipExistingSimulation = true;
cfg.rmt.brainsightSampleIndex = 1;
cfg.rmt.brainsightWhich = 'samples';
cfg.rmt.brainsightUseSimnibsPython = true;

% Dose optimization
cfg.dose.didtRangeAperUs = 1:1:155;
cfg.dose.minHotSpotSizeMM2 = 50;
cfg.dose.outDirName = 'Dose';
cfg.dose.selectionMetric = 'on_target';
cfg.dose.selectionMode = 'pareto';
cfg.dose.selectionWeights = [0.5 0.3 0.2]; % [onTarget offTarget hotspot]

end
