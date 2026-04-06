function cfg = tans_workflow_defaults(Subdir)
%TANS_WORKFLOW_DEFAULTS Shared default settings for the targeting workflow.

if nargin < 1 || isempty(Subdir)
    Subdir = pwd;
end

cfg = struct;
cfg.Subdir = Subdir;

cfg.headmodel.t1File = 'T1w.nii.gz';
cfg.headmodel.t2File = 'T2w.nii.gz';
cfg.headmodel.surfaceTypes = {'pial', 'white', 'midthickness'};
cfg.headmodel.hemispheres = {'L', 'R'};
cfg.headmodel.skipExistingHeadModel = true;
cfg.headmodel.overwriteExistingHeadModel = false;
cfg.headmodel.skipExistingNativeSurfaces = true;
cfg.headmodel.skinSmoothingStrength = 0.50;
cfg.headmodel.skinSmoothingIterations = 10;
cfg.headmodel.grayMatterSmoothingStrength = 0.50;
cfg.headmodel.grayMatterSmoothingIterations = 10;

cfg.target.thresholdPercentile = 95;
cfg.target.medialWallDistanceMM = 10;
cfg.target.useAvoidance = true;
cfg.target.maxCandidateTargets = 1;

cfg.search.gridRadiusMM = 30;
cfg.search.gridSpacingMM = 2;

cfg.simnibs.coilRelativePath = fullfile('simnibs_env', 'lib', 'python3.11', 'site-packages', ...
    'simnibs', 'resources', 'coil_models', 'Drakaki_BrainStim_2022', 'MagVenture_Cool-B65.ccd');
cfg.simnibs.distanceToScalpMM = 1;
cfg.simnibs.angleResolutionDegrees = 30;
cfg.simnibs.nThreads = 20;
cfg.simnibs.didtAperUs = 1e6;

cfg.optimize.percentileThresholds = linspace(99.9, 99, 10);
cfg.optimize.angleResolutionDegrees = 5;
cfg.optimize.uncertaintyMM = 5;
cfg.optimize.mapScalePercentile = 99;
cfg.optimize.rngSeed = 44;
cfg.optimize.surfaceSmoothingFactor = 0.85;
cfg.optimize.metricDilateMM = 2;

cfg.tolerability.dataFile = '';
cfg.tolerability.labelColumn = 'RefLocation';
cfg.tolerability.xColumn = 'x';
cfg.tolerability.yColumn = 'y';
cfg.tolerability.zColumn = 'z';
cfg.tolerability.siteLabelColumn = 'site_label';
cfg.tolerability.metricColumns = {'Pain', 'Twitches', 'Visible.twitch'};
cfg.tolerability.eegPositionsFile = '';
cfg.tolerability.interpolationMethod = 'idw';
cfg.tolerability.idwPower = 2;
cfg.tolerability.nNeighbors = 6;
cfg.tolerability.extrapolationLimitMode = 'mean_nearest_neighbor';
cfg.tolerability.maxExtrapolationDistanceMM = [];
cfg.tolerability.primaryMetric = 'Pain';
cfg.tolerability.lowerIsBetter = true;

cfg.export.writeBrainsightTxt = true;
cfg.export.optimizeXfmFileName = '';
cfg.export.brainsightFileName = 'OptimalTrajectoryBS.txt';
end
