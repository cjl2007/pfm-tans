function tans_dose(magnE,VertexSurfaceArea,DiDt,AbsoluteThreshold,MinHotSpotSize,TargetNetwork,AvoidanceRegion,FunctionalNetworks,NetworkLabels,OutDir,Paths,varargin)
%TANS_DOSE Evaluate dose-response for optimized coil placement.
%
% Goal
%   Scale optimized-placement E-field maps over a dI/dt range, quantify
%   on-target versus avoidance engagement above an absolute threshold, and
%   select the best dose subject to minimum hotspot-size constraints.
%
% Usage
%   tans_dose(magnE, VertexSurfaceArea, DiDt, AbsoluteThreshold, ...
%       MinHotSpotSize, TargetNetwork, AvoidanceRegion, FunctionalNetworks, ...
%       NetworkLabels, OutDir, Paths)
%
% Inputs
%   magnE (char/string/struct)
%       Optimized-placement E-field map (single-column CIFTI).
%   VertexSurfaceArea (char/string/struct)
%       Per-vertex cortical surface area map.
%   DiDt (vector, A/s)
%       dI/dt values to test.
%   AbsoluteThreshold (scalar, V/m)
%       Suprathreshold cutoff used for hotspot definition.
%   MinHotSpotSize (scalar, mm^2)
%       Minimum allowed suprathreshold hotspot area.
%   TargetNetwork (struct)
%       Target map (nonzero values increase on-target score).
%   AvoidanceRegion (struct or [])
%       Off-target map (nonzero values increase penalty).
%   FunctionalNetworks (struct)
%       Integer-labeled network assignment map for reporting.
%   NetworkLabels (char/string/struct/table)
%       Label/color source. Accepts spreadsheet path, `.mat` (e.g.,
%       Priors.mat-style label/color data), or in-memory struct/table.
%   OutDir (char/string)
%       Output directory.
%   Paths (cellstr)
%       Dependency roots added to MATLAB path.
%   varargin
%       Optional struct with fields:
%       `mapMode` {'auto','binary','probabilistic'} (default 'auto'),
%       `mapScalePercentile` (default 99). For probabilistic maps, weights
%       are clipped after scaling by this percentile.
%       `selectionMetric` {'on_target','penalized'} (default 'on_target').
%       `selectionMode` {'single_metric','pareto'} (default 'single_metric').
%       `selectionWeights` [wOn wOff wHot] (default [0.4 0.3 0.3]) for
%       Pareto tie-breaking toward the ideal point (on-target high,
%       off-target low, hotspot high).
%       `diDtUnits` {'A/us','A/s'} (default 'A/us').
%       `referenceDiDtAperUs` (default 1): dI/dt level represented by the
%       input magnE map used as scaling baseline.
%
% Outputs
%   None (writes dose-response CIFTI/figure/text outputs to `OutDir`).
%
% Side Effects
%   Creates output files/directories, executes `wb_command` and shell
%   commands for temporary file handling and label conversion, and writes
%   QC plots including a stacked network-contribution bar when Priors
%   spatial maps are available.

% add some directories 
% to the search path;
for i = 1:length(Paths)
addpath(genpath(Paths{i})); % 
end

opts = struct;
opts.mapMode = 'auto';
opts.mapScalePercentile = 99;
opts.selectionMetric = 'on_target';
opts.selectionMode = 'single_metric';
opts.selectionWeights = [0.4 0.3 0.3];
opts.diDtUnits = 'A/us';
opts.referenceDiDtAperUs = 1;
if ~isempty(varargin) && isstruct(varargin{1})
    u = varargin{1};
    fn = fieldnames(u);
    for k = 1:numel(fn)
        opts.(fn{k}) = u.(fn{k});
    end
end

selMetric = lower(char(string(opts.selectionMetric)));
assert(ismember(selMetric, {'on_target','penalized'}), ...
    'selectionMetric must be on_target or penalized.');
selMode = lower(char(string(opts.selectionMode)));
assert(ismember(selMode, {'single_metric','pareto'}), ...
    'selectionMode must be single_metric or pareto.');
assert(isnumeric(opts.selectionWeights) && numel(opts.selectionWeights) == 3, ...
    'selectionWeights must be a numeric vector [wOn wOff wHot].');
selW = double(opts.selectionWeights(:)');
if sum(selW) <= 0
    selW = [0.4 0.3 0.3];
end
selW = selW ./ sum(selW);
unitStr = lower(char(string(opts.diDtUnits)));
assert(ismember(unitStr, {'a/us','a/s'}), 'diDtUnits must be A/us or A/s.');
assert(isnumeric(opts.referenceDiDtAperUs) && isscalar(opts.referenceDiDtAperUs) && opts.referenceDiDtAperUs > 0, ...
    'referenceDiDtAperUs must be a positive scalar.');

% make the output directory;
system(['mkdir -p ' OutDir]);

% read in 
% the E-field;
if isstring(magnE); magnE = char(magnE); end
if ischar(magnE)
    magnE = ft_read_cifti_mod(magnE);
end

% read in the
% surface area;
if isstring(VertexSurfaceArea); VertexSurfaceArea = char(VertexSurfaceArea); end
if ischar(VertexSurfaceArea)
    VertexSurfaceArea = ft_read_cifti_mod(VertexSurfaceArea);
end

cortexN = nnz(magnE.brainstructure > 0 & magnE.brainstructure < 3);
vertexArea = VertexSurfaceArea.data(1:cortexN,1);
if isempty(AvoidanceRegion)
    AvoidanceRegion = TargetNetwork; % preallocate
    AvoidanceRegion.data = zeros(size(AvoidanceRegion.data,1));
end
targetWeights = i_prepare_map_weights(TargetNetwork.data(1:cortexN,1), opts.mapMode, opts.mapScalePercentile);
avoidWeights = i_prepare_map_weights(AvoidanceRegion.data(1:cortexN,1), opts.mapMode, opts.mapScalePercentile);
targetMask = targetWeights > 0;

% normalize DiDt input to A/us, then scale relative to input-map baseline.
if strcmp(unitStr, 'a/s')
    DiDtAperUs = DiDt(:)' / 1e6;
else
    DiDtAperUs = DiDt(:)';
end
scale = DiDtAperUs / opts.referenceDiDtAperUs;
baseMagnE = magnE.data(:,1);
for i = 1:length(scale)
    magnE.data(:,i) = baseMagnE * scale(i);
end

% write out the cifti file; each column == a different stimulation intensity level
ft_write_cifti_mod([OutDir '/magnE_BestCoilCenter+BestOrientation_AllStimulationItensities.dtseries.nii'],magnE);
system(['echo ' num2str(DiDtAperUs) ' > ' OutDir '/DiDt.txt']); % write out the intensities used (A/us)

% if no avoidance
% region is specified;
avoidMask = avoidWeights > 0;

% preallocate;
OnTarget = zeros(size(magnE.data,2),1); % "OnTaget" variable (% of E-field hotspot that contains target network vertices);
Penalty = zeros(size(magnE.data,2),1); % "Penalty" variable (% of E-field hotspot that contains avoidance region / network vertices);
HotSpotSize = zeros(size(magnE.data,2),1);

% preallocate;
O = TargetNetwork;
O.data = zeros(size(O.data,1),length(DiDt)); % clear contents of the file;

% log the functional networks inside the blank cifti multiple times;
O.data(1:cortexN,1:length(DiDt)) = repmat(FunctionalNetworks.data(1:cortexN,1),[1 length(DiDt)]);
 
% sweep the range of
% stimulation intensities
for t = 1:size(magnE.data,2)
    
    HotSpot = magnE.data(1:cortexN,t) >= AbsoluteThreshold; % this is the hotspot (suprathreshold portion of e-field);
    HotSpotSize(t) = sum(vertexArea(HotSpot)); % this is the total surface area of the hotspot 
    
    % calculate proportion of the suprathreshold E-field that is on-target
    den = sum(vertexArea(HotSpot));
    if den == 0
        OnTarget(t,1) = 0;
        Penalty(t,1) = 0;
    else
        OnTarget(t,1) = sum(vertexArea(HotSpot & targetMask) .* targetWeights(HotSpot & targetMask)) / den;
        Penalty(t,1) = sum(vertexArea(HotSpot & avoidMask) .* avoidWeights(HotSpot & avoidMask)) / den;
    end

    % discard functional networks
    % outside of E-field hotspot;
    O.data(HotSpot==0,t)=0;
    
end

% write out the stimulated networks;
if isfield(O, 'mapname')
    O = i_ensure_mapnames(O, size(O.data,2), 'DoseLevel');
end
stimBase = [OutDir '/StimulatedNetworks'];
ft_write_cifti_mod(stimBase,O);
stimFile = i_find_written_cifti(stimBase);

labelRows = i_build_network_label_rows(NetworkLabels, FunctionalNetworks, cortexN);
i_write_label_list_file(fullfile(OutDir, 'LabelListFile.txt'), labelRows);

% make dlabel cifti file summarizing which networks were stimulated;
system(['wb_command -cifti-label-import ' stimFile ' ' OutDir '/LabelListFile.txt ' OutDir '/StimulatedNetworks_AbsThreshold_' num2str(AbsoluteThreshold) 'Vm_MinHotSpotSize_' num2str(MinHotSpotSize) 'mm2.dlabel.nii -discard-others']);
if exist(stimFile, 'file') == 2
    delete(stimFile);
end
system(['rm ' OutDir '/LabelListFile.txt']); % clear some intermediate files;

OnTarget_Constrained = OnTarget;
OnTarget_Constrained(HotSpotSize < MinHotSpotSize) = 0;
PenalizedOnTarget = OnTarget - Penalty;
PenalizedOnTarget(HotSpotSize < MinHotSpotSize) = 0;

if strcmp(selMetric, 'penalized')
    baseSelectionScore = PenalizedOnTarget;
else
    baseSelectionScore = OnTarget_Constrained;
end

% first suprathreshold dose (minimum stimulation with any hotspot).
minIdx = find(HotSpotSize > 0, 1, 'first');
if isempty(minIdx)
    minIdx = 1;
end

if strcmp(selMode, 'pareto')
    feasible = (HotSpotSize >= MinHotSpotSize) & isfinite(OnTarget) & isfinite(Penalty) & isfinite(HotSpotSize);
    if any(feasible)
        idxFeasible = find(feasible);
        onF = OnTarget(idxFeasible);
        offF = Penalty(idxFeasible);
        hotF = HotSpotSize(idxFeasible);
        paretoLocal = i_pareto_front_3obj(onF, offF, hotF);
        paretoGlobal = idxFeasible(paretoLocal);
        % Choose Pareto point nearest to ideal (1,1,1) after objective normalization.
        onN = i_minmax_norm(OnTarget(paretoGlobal));
        offN = i_minmax_norm(-Penalty(paretoGlobal)); % lower penalty is better
        hotN = i_minmax_norm(HotSpotSize(paretoGlobal));
        utility = selW(1) * onN + selW(2) * offN + selW(3) * hotN;
        [~, bestLocal] = max(utility);
        bestIdx = paretoGlobal(bestLocal);
        % tie-break toward lower dose if utility ties
        tie = paretoGlobal(abs(utility - utility(bestLocal)) < 1e-12);
        bestIdx = min(tie);
    else
        bestIdx = minIdx;
    end
else
    % legacy single-metric behavior
    bestIdx = find(baseSelectionScore == max(baseSelectionScore));
    bestIdx = bestIdx(1); % tie-breaker: choose the lower dose level
end
magnE.data = magnE.data(:,bestIdx);

% write out the cifti file; 
ft_write_cifti_mod([OutDir '/magnE_BestCoilCenter+BestOrientation+BestDose.dtseries.nii'],magnE);
TargetDose = DiDtAperUs(bestIdx);
MinDose = DiDtAperUs(minIdx);
system(['echo ' num2str(MinDose) ' > ' OutDir '/MinDose.txt']);   % minimum suprathreshold dose (A/us).
system(['echo ' num2str(TargetDose) ' > ' OutDir '/TargetDose.txt']); % Pareto/selected target dose (A/us).

% make some figures;

H = figure; % prellocate parent figure
set(H,'position',[1 1 350 225]);
hold on;

% plot the results;
xx = 1:numel(DiDtAperUs);
plot(xx, OnTarget(:)' * 100, '-k', 'LineWidth', 1.5);
plot(bestIdx, OnTarget(bestIdx) * 100, 'ro', 'MarkerFaceColor', 'r', 'MarkerSize', 5);
xline(bestIdx, 'r--', 'LineWidth', 1);
plot(minIdx, OnTarget(minIdx) * 100, 'bo', 'MarkerFaceColor', 'b', 'MarkerSize', 5);
xline(minIdx, 'b--', 'LineWidth', 1);
text(bestIdx, min(100, OnTarget(bestIdx) * 100 + 5), ...
    sprintf('Target: %.1f A/\\mus', TargetDose), ...
    'Color', 'r', 'FontSize', 9, 'HorizontalAlignment', 'left', 'VerticalAlignment', 'bottom');
text(minIdx, max(0, OnTarget(minIdx) * 100 - 7), ...
    sprintf('Min: %.1f A/\\mus', MinDose), ...
    'Color', 'b', 'FontSize', 9, 'HorizontalAlignment', 'left', 'VerticalAlignment', 'top');

% make it "pretty";
xlim([0 length(DiDt)]);
ylim([0 100]);
yticks(0:20:100);
xlabel('dI/dt (A/\mus)');
ylabel('% On-Target')
xtIdx = 1:round(length(DiDt)/20):length(DiDt);
xticks(xtIdx);
xticklabels(round(DiDtAperUs(xtIdx)));
xtickangle(90);
grid on;
set(gca,'FontName','Arial','FontSize',12,'TickLength',[0 0]);
saveas(gcf,[OutDir '/OnTarget_AbsThreshold_' num2str(AbsoluteThreshold) 'Vm_MinHotSpotSize_' num2str(MinHotSpotSize) 'mm2.png']); 
hold off;
close all;

H = figure; % prellocate parent figure
set(H,'position',[1 1 350 225]);
hold on;

% plot the results;
OnTarget(isnan(OnTarget))=0;
scatter(HotSpotSize,OnTarget * 100,[],DiDtAperUs,'filled'); 
colormap(jet);
h = colorbar; 
h.Label.String = 'dI/dt (A/\mus)';
plot(HotSpotSize(bestIdx), OnTarget(bestIdx) * 100, 'ko', 'MarkerFaceColor', 'w', 'MarkerSize', 6);
plot(HotSpotSize(minIdx), OnTarget(minIdx) * 100, 'ks', 'MarkerFaceColor', 'c', 'MarkerSize', 6);
text(HotSpotSize(bestIdx), min(100, OnTarget(bestIdx) * 100 + 5), ...
    sprintf('Target: %.1f A/\\mus', TargetDose), ...
    'Color', 'k', 'FontSize', 9, 'HorizontalAlignment', 'left', 'VerticalAlignment', 'bottom');
text(HotSpotSize(minIdx), max(0, OnTarget(minIdx) * 100 - 7), ...
    sprintf('Min: %.1f A/\\mus', MinDose), ...
    'Color', 'k', 'FontSize', 9, 'HorizontalAlignment', 'left', 'VerticalAlignment', 'top');

% make it "pretty";
ylim([0 100]);
yticks(0:20:100);
xlabel('Hotspot size (mm2)');
ylabel('% On-Target')
grid on;
set(gca,'FontName','Arial','FontSize',12,'TickLength',[0 0]);
saveas(gcf,[OutDir '/OnTarget_vs_HotspotSize_Curve_AbsThreshold_' num2str(AbsoluteThreshold) 'Vm.png']); 
hold off;
close all;

end

function idx = i_pareto_front_3obj(onVals, offVals, hotVals)
% Maximization objectives: onVals, -offVals, hotVals.
n = numel(onVals);
isDominated = false(n,1);
for i = 1:n
    if isDominated(i)
        continue;
    end
    for j = 1:n
        if i == j
            continue;
        end
        betterOrEqual = (onVals(j) >= onVals(i)) && (offVals(j) <= offVals(i)) && (hotVals(j) >= hotVals(i));
        strictlyBetter = (onVals(j) > onVals(i)) || (offVals(j) < offVals(i)) || (hotVals(j) > hotVals(i));
        if betterOrEqual && strictlyBetter
            isDominated(i) = true;
            break;
        end
    end
end
idx = find(~isDominated);
end

function x = i_minmax_norm(v)
v = double(v(:));
if isempty(v)
    x = v;
    return;
end
vmin = min(v);
vmax = max(v);
if vmax <= vmin
    x = ones(size(v));
else
    x = (v - vmin) ./ (vmax - vmin);
end
end

function C = i_ensure_mapnames(C, nCols, baseName)
if ~isfield(C, 'mapname') || isempty(C.mapname)
    C.mapname = cell(1, nCols);
    for i = 1:nCols
        C.mapname{i} = sprintf('%s_%03d', baseName, i);
    end
    return;
end

if ischar(C.mapname)
    C.mapname = {C.mapname};
elseif isstring(C.mapname)
    C.mapname = cellstr(C.mapname(:)');
end

if numel(C.mapname) < nCols
    old = C.mapname;
    C.mapname = cell(1, nCols);
    for i = 1:numel(old)
        C.mapname{i} = char(string(old{i}));
    end
    for i = numel(old)+1:nCols
        C.mapname{i} = sprintf('%s_%03d', baseName, i);
    end
elseif numel(C.mapname) > nCols
    C.mapname = C.mapname(1:nCols);
end
end

function weights = i_prepare_map_weights(v, mode, scalePct)
v = double(v(:));
mode = lower(strtrim(mode));

isBinary = i_is_binary_like(v);
if strcmp(mode, 'auto')
    if isBinary
        mode = 'binary';
    else
        mode = 'probabilistic';
    end
end

switch mode
    case 'binary'
        weights = zeros(size(v));
        weights(v > 0) = 1;
    case 'probabilistic'
        weights = v;
        weights(weights < 0) = 0;
        pos = weights(weights > 0);
        if ~isempty(pos)
            scale = prctile(pos, scalePct);
            if scale > 0
                weights = weights ./ scale;
            end
        end
        weights(weights > 1) = 1;
    otherwise
        error('Unsupported mapMode: %s', mode);
end
end

function tf = i_is_binary_like(v)
u = unique(v(isfinite(v)));
if isempty(u)
    tf = true;
    return;
end
tf = all(abs(u - round(u)) < 1e-9) && all(ismember(round(u), [0 1]));
end

function rows = i_build_network_label_rows(labelSource, FunctionalNetworks, cortexN)
rows = [];
if isempty(labelSource)
    rows = i_fallback_label_rows(FunctionalNetworks, cortexN);
    return;
end

if isstring(labelSource); labelSource = char(labelSource); end
if ischar(labelSource)
    [~,~,ext] = fileparts(labelSource);
    switch lower(ext)
        case '.mat'
            S = load(labelSource);
            rows = i_extract_rows_from_mat(S);
        otherwise
            rows = i_extract_rows_from_xls(labelSource);
    end
elseif isstruct(labelSource)
    rows = i_extract_rows_from_mat(labelSource);
elseif istable(labelSource)
    rows = i_extract_rows_from_table(labelSource);
end

if isempty(rows)
    rows = i_fallback_label_rows(FunctionalNetworks, cortexN);
end
end

function rows = i_extract_rows_from_xls(xlsPath)
rows = [];
try
    T = readtable(xlsPath);
    rows = i_extract_rows_from_table(T);
catch
end
end

function rows = i_extract_rows_from_mat(S)
rows = [];
if isfield(S, 'NetworkLabels')
    rows = i_extract_rows_from_table(S.NetworkLabels);
    if ~isempty(rows); return; end
end
if isfield(S, 'Labels') && isfield(S, 'Colors')
    names = S.Labels;
    cols = S.Colors;
    rows = i_rows_from_name_color(names, cols);
    if ~isempty(rows); return; end
end
if isfield(S, 'NetworkNames') && isfield(S, 'NetworkColors')
    rows = i_rows_from_name_color(S.NetworkNames, S.NetworkColors);
end
end

function rows = i_extract_rows_from_table(T)
rows = [];
if isempty(T) || ~istable(T); return; end
vn = T.Properties.VariableNames;
idCol = i_find_var(vn, {'Index','ID','NetworkID','LabelID','Value'});
nameCol = i_find_var(vn, {'Network','Name','Label','NetworkName'});
rCol = i_find_var(vn, {'R','Red'});
gCol = i_find_var(vn, {'G','Green'});
bCol = i_find_var(vn, {'B','Blue'});
if isempty(nameCol) || isempty(rCol) || isempty(gCol) || isempty(bCol)
    return;
end
names = T.(nameCol);
cols = [T.(rCol), T.(gCol), T.(bCol)];
ids = [];
if ~isempty(idCol)
    ids = T.(idCol);
end
rows = i_rows_from_name_color(names, cols, ids);
end

function rows = i_rows_from_name_color(names, cols, ids)
rows = [];
if isempty(names) || isempty(cols); return; end
if nargin < 3
    ids = [];
end
n = min(numel(names), size(cols,1));
rows = cell(n,5);
for i = 1:n
    if iscell(names)
        rows{i,1} = char(string(names{i}));
    else
        rows{i,1} = char(string(names(i)));
    end
    if isempty(ids)
        rows{i,2} = i;
    else
        if iscell(ids)
            rows{i,2} = double(ids{i});
        else
            rows{i,2} = double(ids(i));
        end
        if ~isfinite(rows{i,2})
            rows{i,2} = i;
        end
    end
    rows{i,3} = round(cols(i,1));
    rows{i,4} = round(cols(i,2));
    rows{i,5} = round(cols(i,3));
end
end

function rows = i_fallback_label_rows(FunctionalNetworks, cortexN)
ids = unique(FunctionalNetworks.data(1:cortexN,1));
ids = ids(ids > 0);
n = numel(ids);
if n == 0
    rows = {'Network1', 1, 255, 0, 0};
    return;
end
cmap = round(255 * hsv(max(n, 2)));
rows = cell(n,5);
for i = 1:n
    rows{i,1} = sprintf('Network%d', ids(i));
    rows{i,2} = ids(i);
    rows{i,3} = cmap(i,1);
    rows{i,4} = cmap(i,2);
    rows{i,5} = cmap(i,3);
end
end

function i_write_label_list_file(path, rows)
fid = fopen(path, 'w');
assert(fid > 0, 'Could not create label list file: %s', path);
cleaner = onCleanup(@() fclose(fid)); %#ok<NASGU>
for i = 1:size(rows,1)
    fprintf(fid, '%s\n', rows{i,1});
    fprintf(fid, '%d %d %d %d 255\n', rows{i,2}, rows{i,3}, rows{i,4}, rows{i,5});
end
end

function name = i_find_var(vn, candidates)
name = '';
for i = 1:numel(candidates)
    idx = find(strcmpi(vn, candidates{i}), 1, 'first');
    if ~isempty(idx)
        name = vn{idx};
        return;
    end
end
end

function p = i_find_written_cifti(base)
candidates = { ...
    [base '.dtseries.nii'], ...
    [base '.dscalar.nii'], ...
    [base '.dlabel.nii']};
for i = 1:numel(candidates)
    if exist(candidates{i}, 'file') == 2
        p = candidates{i};
        return;
    end
end
error('Could not find written CIFTI file for base path: %s', base);
end
