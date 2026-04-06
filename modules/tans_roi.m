function roiResult = tans_roi(TargetNetwork, MidthickSurfs, VertexSurfaceArea, Sulc, SearchSpace, OutDir, Paths, targetCfg)
%TANS_ROI Build and rank candidate target ROIs for downstream processing.

if nargin < 8 || isempty(targetCfg)
    targetCfg = struct;
end
medialWallDistanceMM = i_get_opt(targetCfg, 'medialWallDistanceMM', 10);
maxCandidateTargets = i_get_opt(targetCfg, 'maxCandidateTargets', 1);
assert(maxCandidateTargets >= 1 && mod(maxCandidateTargets, 1) == 0, ...
    'targetCfg.maxCandidateTargets must be an integer >= 1.');

for i = 1:length(Paths)
    addpath(genpath(Paths{i}));
end

LH = gifti(MidthickSurfs{1});
RH = gifti(MidthickSurfs{2});

BrainStructure = TargetNetwork.brainstructure;
SurfaceCoordinates = [LH.vertices; RH.vertices];
SurfaceIndex = BrainStructure > 0 & BrainStructure < 3;
cortexN = nnz(SurfaceIndex);
BrainStructure(BrainStructure == -1) = [];
SurfaceIndex = SurfaceIndex(1:size(SurfaceCoordinates,1));
SurfaceCoordinates = SurfaceCoordinates(SurfaceIndex,:);

D = pdist2(SurfaceCoordinates, SurfaceCoordinates);
D(BrainStructure == 1, BrainStructure == 1) = nan;
D(BrainStructure == 2, BrainStructure == 2) = nan;
MedialWallVertices = find(min(D, [], 2) < medialWallDistanceMM);

roiDir = fullfile(OutDir, 'ROI');
if ~exist(roiDir, 'dir')
    mkdir(roiDir);
end

if isempty(SearchSpace)
    SearchSpace = TargetNetwork;
    SearchSpace.data = zeros(size(SearchSpace.data,1));
end
ft_write_cifti_mod(fullfile(roiDir, 'SearchSpace'), SearchSpace);

TargetNetwork.data = TargetNetwork.data ~= 0;
O = TargetNetwork;
O.data = zeros(size(TargetNetwork.data));
O.data(TargetNetwork.data == 1) = 1;
O.data(cortexN+1:end) = 0;
ft_write_cifti_mod(fullfile(roiDir, 'TargetNetwork'), O);

system(sprintf('echo Target Network > %s', fullfile(roiDir, 'Labels.txt')));
system(sprintf('echo 1 0 0 0 255 >> %s', fullfile(roiDir, 'Labels.txt')));
system(sprintf('wb_command -cifti-label-import %s %s %s -discard-others', ...
    fullfile(roiDir, 'TargetNetwork.dtseries.nii'), ...
    fullfile(roiDir, 'Labels.txt'), ...
    fullfile(roiDir, 'TargetNetwork.dlabel.nii')));
system(sprintf('wb_command -cifti-label-to-border %s -border %s %s', ...
    fullfile(roiDir, 'TargetNetwork.dlabel.nii'), ...
    MidthickSurfs{1}, fullfile(roiDir, 'TargetNetwork.L.border')));
system(sprintf('wb_command -cifti-label-to-border %s -border %s %s', ...
    fullfile(roiDir, 'TargetNetwork.dlabel.nii'), ...
    MidthickSurfs{2}, fullfile(roiDir, 'TargetNetwork.R.border')));
system(sprintf('rm %s %s', fullfile(roiDir, 'Labels.txt'), fullfile(roiDir, 'TargetNetwork.dlabel.nii')));

TargetNetwork.data(SearchSpace.data == 0) = 0;
O = TargetNetwork;
O.data = zeros(size(TargetNetwork.data));
O.data(TargetNetwork.data == 1) = 1;
O.data(cortexN+1:end) = 0;
ft_write_cifti_mod(fullfile(roiDir, 'TargetNetwork+SearchSpace'), O);

TargetNetwork.data(Sulc.data < 0) = 0;
TargetNetwork.data(MedialWallVertices) = 0;
O = TargetNetwork;
O.data = zeros(size(TargetNetwork.data));
O.data(TargetNetwork.data == 1) = 1;
O.data(cortexN+1:end) = 0;
ft_write_cifti_mod(fullfile(roiDir, 'TargetNetwork+SearchSpace+SulcalMask'), O);

clusterFile = fullfile(roiDir, 'TargetNetwork+SearchSpace+SulcalMask+Clusters.dtseries.nii');
system(sprintf(['wb_command -cifti-find-clusters %s 0 0 0 0 COLUMN %s ' ...
    '-left-surface %s -right-surface %s'], ...
    fullfile(roiDir, 'TargetNetwork+SearchSpace+SulcalMask.dtseries.nii'), ...
    clusterFile, MidthickSurfs{1}, MidthickSurfs{2}));

Clusters = ft_read_cifti_mod(clusterFile);
uClusters = unique(nonzeros(Clusters.data));
nClusters = numel(uClusters);

if nClusters == 0
    roiResult = struct;
    roiResult.candidates = struct([]);
    roiResult.n_candidates = 0;
    roiResult.max_candidate_targets = maxCandidateTargets;
    return;
end

ClusterSize = zeros(nClusters, 1);
for i = 1:nClusters
    clusterLabel = uClusters(i);
    ClusterSize(i) = sum(VertexSurfaceArea.data(Clusters.data == clusterLabel));
end

[sortedSizes, order] = sort(ClusterSize, 'descend');
sortedClusters = uClusters(order);

clusterMetric = Clusters;
clusterMetric.data = zeros(size(clusterMetric.data));
for i = 1:nClusters
    clusterMetric.data(Clusters.data == sortedClusters(i), 1) = sortedSizes(i);
end
ft_write_cifti_mod(fullfile(roiDir, 'TargetNetwork+SearchSpace+SulcalMask+ClusterSizes'), clusterMetric);

nKeep = min(maxCandidateTargets, nClusters);
candidateList = repmat(struct, nKeep, 1);
for i = 1:nKeep
    clusterLabel = sortedClusters(i);
    targetPatch = false(cortexN, 1);
    targetPatch(:) = Clusters.data(1:cortexN) == clusterLabel;

    patchStruct = Clusters;
    patchStruct.data = zeros(size(Clusters.data,1), 1);
    patchStruct.data(find(targetPatch), 1) = 1; %#ok<FNDSB>

    candidateLabel = sprintf('Candidate%d', i);
    candidateDir = fullfile(OutDir, candidateLabel);
    candidateRoiDir = fullfile(candidateDir, 'ROI');
    if ~exist(candidateRoiDir, 'dir')
        mkdir(candidateRoiDir);
    end

    ft_write_cifti_mod(fullfile(candidateRoiDir, 'TargetNetworkPatch'), patchStruct);
    ft_write_cifti_mod(fullfile(candidateRoiDir, 'CandidateClusterMask'), patchStruct);

    candidateList(i).rank = i;
    candidateList(i).label = candidateLabel;
    candidateList(i).cluster_id = clusterLabel;
    candidateList(i).cluster_size_mm2 = sortedSizes(i);
    candidateList(i).outDir = candidateDir;
    candidateList(i).roiDir = candidateRoiDir;
    candidateList(i).patchFile = fullfile(candidateRoiDir, 'TargetNetworkPatch.dtseries.nii');
    candidateList(i).patch_struct = patchStruct;
end

summaryPath = fullfile(roiDir, 'CandidateClusters.tsv');
fid = fopen(summaryPath, 'w');
assert(fid > 0, 'Unable to open candidate summary file: %s', summaryPath);
cleanupObj = onCleanup(@() fclose(fid));
fprintf(fid, 'candidate_rank\tcluster_id\tcluster_size_mm2\tcandidate_dir\n');
for i = 1:nKeep
    fprintf(fid, '%d\t%d\t%s\t%s\n', candidateList(i).rank, candidateList(i).cluster_id, ...
        num2str(candidateList(i).cluster_size_mm2), candidateList(i).outDir);
end

roiResult = struct;
roiResult.outDir = OutDir;
roiResult.roiDir = roiDir;
roiResult.n_candidates = nKeep;
roiResult.max_candidate_targets = maxCandidateTargets;
roiResult.candidates = candidateList;
roiResult.all_cluster_labels = sortedClusters;
roiResult.all_cluster_sizes_mm2 = sortedSizes;
end

function out = i_get_opt(S, field, defaultVal)
if isfield(S, field) && ~isempty(S.(field))
    out = S.(field);
else
    out = defaultVal;
end
end
