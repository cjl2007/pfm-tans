function [SubSampledSearchGrid,FullSearchGrid] = tans_searchgrid(TargetNetworkPatch,PialSurfs,SkinSurf,GridSpacing,SearchGridRadius,OutDir,Paths)
%TANS_SEARCHGRID Generate full and subsampled scalp search grids.
%
% Goal
%   Identify scalp vertices near the target-patch centroid and construct a
%   manageable subsampled grid for downstream E-field simulations.
%
% Usage
%   [SubSampledSearchGrid, FullSearchGrid] = tans_searchgrid( ...
%       TargetNetworkPatch, PialSurfs, SkinSurf, GridSpacing, ...
%       SearchGridRadius, OutDir, Paths)
%
% Inputs
%   TargetNetworkPatch (struct)
%       CIFTI-like binary patch produced by `tans_roi`.
%   PialSurfs (1x2 cellstr)
%       Left/right 32k FS_LR pial surface paths.
%   SkinSurf (char/string)
%       Scalp surface `.surf.gii` path.
%   GridSpacing (numeric scalar)
%       Approximate minimum spacing between subsampled grid vertices (mm).
%   SearchGridRadius (numeric scalar)
%       Geodesic radius around scalp vertex above ROI centroid (mm).
%   OutDir (char/string)
%       Base output directory.
%   Paths (cellstr)
%       Paths added to MATLAB path for dependencies.
%
% Outputs
%   SubSampledSearchGrid (N x 3 double)
%       Subsampled scalp coordinates used for simulation.
%   FullSearchGrid (M x 3 double)
%       Full (pre-subsampling) scalp coordinates inside search radius.
%
% Side Effects
%   Creates `<OutDir>/SearchGrid/`, writes GIFTI/`.mat` files, and invokes
%   `wb_command -surface-geodesic-distance`.

% add some directories 
% to the search path;
for i = 1:length(Paths)
addpath(genpath(Paths{i})); % 
end

% make search grid directory;
mkdir([OutDir '/SearchGrid/']);

% load pial surfaces; 
LH = gifti(PialSurfs{1});
RH = gifti(PialSurfs{2});

% extract coordinates for all cortical vertices 
SurfaceCoords = [LH.vertices; RH.vertices]; % combine hemipsheres 
NotMedialWall = TargetNetworkPatch.brainstructure > 0 & TargetNetworkPatch.brainstructure < 3;
NotMedialWall = NotMedialWall(1:size(SurfaceCoords,1));
SurfaceCoords = SurfaceCoords(NotMedialWall,:);

% create a search grid above the target network patch centroid;

% read in the
% skin surf.gii file;
Skin = gifti(SkinSurf);

D = pdist2(Skin.vertices,mean(SurfaceCoords(TargetNetworkPatch.data==1,:))); % log the distances to ROI centroid
VertexDirectlyAbovePatch = find(D==min(D)); % this is the vertex directly above the ROI centroid; this serves as the center point of the search grid we will create
system(['wb_command -surface-geodesic-distance ' SkinSurf ' ' num2str(VertexDirectlyAbovePatch-1) ' ' OutDir '/SearchGrid/DistanceFromSearchGridCenter.shape.gii']);
G = gifti([OutDir '/SearchGrid/DistanceFromSearchGridCenter.shape.gii']); % load temporary file
G.cdata(G.cdata < 0) = 999; % mark bad vertices 

% create a metric file showing the full search grid;
G.cdata(G.cdata>=SearchGridRadius) = 0; 
G.cdata(G.cdata~=0) = 1;
G.cdata(VertexDirectlyAbovePatch) = 1; % include the nearest point as well
save(G,[OutDir '/SearchGrid/FullSearchGrid.shape.gii']); % 

% full search grid 
% coordinates & vertices;
SearchGridVertices = find(G.cdata~=0);
FullSearchGrid = Skin.vertices(SearchGridVertices,:); % 

% preallocate neighbors variable;
V = nan(size(SearchGridVertices,1),10^3); % 10

% sweep through the full search grid
for i = 1:size(SearchGridVertices,1)
    D = pdist2(Skin.vertices(SearchGridVertices(i),:),Skin.vertices);
    tmp = find(D>0 & D<=GridSpacing);
    V(i,1:length(tmp)) = tmp;
end

% preallocate;
SubSample = [];
Neighbors = [];

% sweep all the
% skin vertices
for i = 1:size(V,1)
    if ~ismember(SearchGridVertices(i),Neighbors)
        SubSample = [SubSample SearchGridVertices(i)];
        Neighbors = [Neighbors V(i,2:end)];
    end
end

% this is a sensible subsampling of the
% full search grid that is suggested for following analyses
SubSampledSearchGrid = Skin.vertices(SubSample,:); 

% create a metric file showing the subsampled search grid
G = gifti([OutDir '/SearchGrid/FullSearchGrid.shape.gii']); 
G.cdata = zeros(size(G.cdata)); % blank slate;
G.cdata(SubSample) = 1:length(SubSample); % log serial numbers
save(G,[OutDir '/SearchGrid/SubSampledSearchGrid.shape.gii']); % write out the metric file

% save some variables;
save([OutDir '/SearchGrid/SubSampledSearchGrid'],'SubSampledSearchGrid');
save([OutDir '/SearchGrid/FullSearchGrid'],'FullSearchGrid');

end


