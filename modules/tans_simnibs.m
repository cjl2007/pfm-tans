function tans_simnibs(SearchGridCoords,HeadMesh,CoilModel,AngleResolution,DistanceToScalp,SkinSurf,MidthickSurfs,WhiteSurfs,PialSurfs,MedialWallMasks,nThreads,OutDir,Paths,simnibsCfg)
%TANS_SIMNIBS Run SimNIBS E-field simulations for each search-grid center.
%
% Goal
%   For each coil center in the scalp search grid, simulate multiple coil
%   orientations, map fields to cortical surfaces, and store CIFTI outputs
%   used later by `tans_optimize`.
%
% Usage
%   tans_simnibs(SearchGridCoords, HeadMesh, CoilModel, AngleResolution, ...
%       DistanceToScalp, SkinSurf, MidthickSurfs, WhiteSurfs, PialSurfs, ...
%       MedialWallMasks, nThreads, OutDir, Paths)
%   tans_simnibs(..., simnibsCfg)
%
% Inputs
%   SearchGridCoords (N x 3 double)
%       Coil center coordinates from `tans_searchgrid`.
%   HeadMesh (char/string)
%       SimNIBS tetrahedral head mesh (`.msh`).
%   CoilModel (char/string)
%       Coil model path (e.g., `.ccd`).
%   AngleResolution (numeric scalar)
%       Angular sampling resolution for candidate orientations (degrees).
%   DistanceToScalp (numeric scalar)
%       Coil center offset from scalp (mm).
%   SkinSurf (char/string)
%       Scalp surface `.surf.gii`.
%   MidthickSurfs, WhiteSurfs, PialSurfs, MedialWallMasks (1x2 cellstr)
%       Left/right surface and medial-wall mask paths for surface mapping.
%   nThreads (numeric scalar)
%       MATLAB parallel pool size used by `parfor`.
%   OutDir (char/string)
%       Base output directory.
%   Paths (cellstr)
%       Dependency roots added to MATLAB path.
%   simnibsCfg (struct, optional)
%       Optional overrides including `didtAperUs`.
%
% Outputs
%   None (writes `magnE_*.dtseries.nii` files under `<OutDir>/SearchGrid/`).
%
% Side Effects
%   Starts/deletes a parallel pool, creates/removes per-simulation folders,
%   runs SimNIBS and external shell tools (`fslmerge`, `wb_command`, `mv`,
%   `rm`), and suppresses warnings.

if nargin < 14 || isempty(simnibsCfg)
    simnibsCfg = struct;
end
didtAperUs = i_get_opt(simnibsCfg, 'didtAperUs', 1e6);

% define some directories;
addpath(genpath(Paths{1})); % define the path to SimNibs software
addpath(genpath(Paths{2})); % define the path to the folder containing "ft_read_cifti" / "gifti" functions
warning ('off','all'); % turn off annoying warnings;

% load skin mesh generated
% by "tans_headmodels.m"
SkinSurf = gifti(SkinSurf); 

% define a parpool;
pool = parpool('local',nThreads);

% sweep the search space;
parfor i = 1:size(SearchGridCoords,1)
    
    try % note: sometimes the scalp position is weird and the simulation will fail. 
        % So we have a try/catch here to prevent the whole thing from failing.
    
    % Initialize a session
    s = sim_struct('SESSION');
    
    % Name of head mesh
    s.fnamehead = HeadMesh;
    
    % Output folder
    s.pathfem = [OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/'];
    
    % Initialize a list of TMS simulations
    s.poslist{1} = sim_struct('TMSLIST');
    
    % specify the coil model;
    s.poslist{1}.fnamecoil = CoilModel;
    
    % generate an approximate circle around the center position (this is a bit crude but works fine);
    D = pdist2(SkinSurf.vertices,[SearchGridCoords(i,1), SearchGridCoords(i,2), SearchGridCoords(i,3)]);
    A = find(D < 19); % inner diameter;
    B = find(D < 20); % outer diameter;
    Circle = SkinSurf.vertices(B(~ismember(B,A)),:); % find the difference;
    
    % convert to a single ring (remove any "pile up");
    [~,Circle] = kmeans(Circle,round(360 / AngleResolution));
    
    % sort coordinates in a radial fashion & average across neighboring 
    % positions to achieve more regular spacing between reference points;
    [RefDirs] = SortCircleCoords(Circle,ceil(size(Circle,1) * 0.15)); % 

    RefDirs = RefDirs(1:ceil(size(RefDirs,1)/2),:); % sample one half of the circle; 
    % this reduces the number of simulations you need to run, while still capturing the max on-target value for this coil center. 

    % sweep a range of 
    % coil orientations;
    for ii = 1:length(RefDirs)
    
    % specify & save coil position;
    s.poslist{1}.pos(ii).centre = [SearchGridCoords(i,1), SearchGridCoords(i,2), SearchGridCoords(i,3)];
    s.poslist{1}.pos(ii).pos_ydir = [RefDirs(ii,1), RefDirs(ii,2), RefDirs(ii,3)];
    s.poslist{1}.pos(ii).didt = didtAperUs; % A/us
    s.poslist{1}.pos(ii).distance = DistanceToScalp; % 
    
    end
    
    % write to volume;
    s.map_to_vol = true;
    s.fields = 'e'; % magnE only;
    
    % run the
    % simulation;
    run_simnibs(s);
    
    % merge all the volumes into a single 4D file;
    system(['fslmerge -t ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.nii.gz ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/*magnE.nii.gz']);
    
    % map concatenated volume to the 32k surface;
    system(['wb_command -volume-to-surface-mapping ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.nii.gz  ' MidthickSurfs{1} ' ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.L.32k_fs_LR.shape.gii -ribbon-constrained ' WhiteSurfs{1} ' ' PialSurfs{1}]); % note: on 1/11/22 CJL removed -interpolate ENCLOSING at the end of this command. 
    system(['wb_command -volume-to-surface-mapping ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.nii.gz  ' MidthickSurfs{2} ' ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.R.32k_fs_LR.shape.gii -ribbon-constrained ' WhiteSurfs{2} ' ' PialSurfs{2}]);
    system(['wb_command -metric-mask ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.L.32k_fs_LR.shape.gii ' MedialWallMasks{1} ' ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.L.32k_fs_LR.shape.gii']); 
    system(['wb_command -metric-mask ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.R.32k_fs_LR.shape.gii ' MedialWallMasks{2} ' ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.R.32k_fs_LR.shape.gii']);
    system(['wb_command -cifti-create-dense-timeseries ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.dtseries.nii -left-metric ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.L.32k_fs_LR.shape.gii -roi-left ' MedialWallMasks{1} ' -right-metric ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.R.32k_fs_LR.shape.gii -roi-right ' MedialWallMasks{2}]);
    system(['mv ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/subject_volumes/magnE.dtseries.nii ' OutDir '/SearchGrid/magnE_' sprintf('%05d',i) '.dtseries.nii']);
    system(['mv ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/*.log ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '.log']);

    % remove some large intermediate files to save disk space;
    system(['rm -rf ' OutDir '/SearchGrid/Simulation_' sprintf('%05d',i) '/']);
  
    catch
    end
    
end

function out = i_get_opt(S, field, defaultVal)
if isfield(S, field) && ~isempty(S.(field))
    out = S.(field);
else
    out = defaultVal;
end
end

% delete the
% parpool;
delete(pool);

end

function [Output] = SortCircleCoords(Coords,W)
%SORTCIRCLECOORDS Order/smooth ring coordinates for orientation sampling.

% preallocate;
SortedCoords = zeros(size(Coords));

% preallocate;
Used = [];
Idx = 1;

% sweep the coordinates;
for i = 1:size(Coords,1)
    D = pdist2(Coords(Idx,:),Coords);
    D(Used)=nan;
    Idx = find(D==min(nonzeros(D)));
    Idx = Idx(1); % in case there is more than one;
    SortedCoords(i,:) = Coords(Idx,:);
    Used = [Used Idx];
end

% preallocate;
Output = zeros(size(Coords));

% sweep the coordinates
for i = 0:size(SortedCoords,1)-1
    Idx = circshift(1:size(SortedCoords,1),W + i);
    Output(i+1,:) = mean(SortedCoords(Idx(1:W*2),:));
end

end
