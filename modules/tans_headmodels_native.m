function tans_headmodels_native(Subject,T1w,T2w,OutDir,Paths,headmodelCfg)
%TANS_HEADMODELS_NATIVE Build subject-native head-model assets for PFM-TANS.
%
% Goal
%   Create a SimNIBS head model from anatomical MRI, then generate scalp
%   and gray-matter surface files used downstream for search-grid creation
%   and optimization visualization/export.
%
% Usage
%   tans_headmodels_native(Subject, T1w, T2w, OutDir, Paths)
%   tans_headmodels_native(..., headmodelCfg)
%
% Inputs
%   Subject (char/string)
%       Subject identifier used for SimNIBS output naming.
%   T1w (char/string)
%       Path to T1-weighted anatomical image.
%   T2w (char/string/[])
%       Path to co-registered T2 image (optional; pass [] if unavailable).
%   OutDir (char/string)
%       Base output directory (typically `<Subdir>/tans`).
%   Paths (cellstr)
%       Paths added to MATLAB path (SimNIBS/MSC/PFM-TANS dependencies).
%   headmodelCfg (struct, optional)
%       Optional overrides for smoothing:
%       `skinSmoothingStrength`, `skinSmoothingIterations`,
%       `grayMatterSmoothingStrength`, `grayMatterSmoothingIterations`.
%
% Outputs
%   None (writes files to disk).
%
% Side Effects
%   Creates/overwrites `<OutDir>/HeadModel/` contents, changes working
%   directory to `<OutDir>/HeadModel/`, and executes external commands
%   (`charm`, `mri_binarize`, `fslmaths`, `mri_tessellate`, `mris_convert`,
%   `wb_command`, `gzip`).

if nargin < 6 || isempty(headmodelCfg)
    headmodelCfg = struct;
end

% Defaults preserve current behavior unless overridden in config.
skinSmoothingStrength = i_get_opt(headmodelCfg, 'skinSmoothingStrength', 0.50);
skinSmoothingIterations = i_get_opt(headmodelCfg, 'skinSmoothingIterations', 10);
grayMatterSmoothingStrength = i_get_opt(headmodelCfg, 'grayMatterSmoothingStrength', skinSmoothingStrength);
grayMatterSmoothingIterations = i_get_opt(headmodelCfg, 'grayMatterSmoothingIterations', skinSmoothingIterations);

% add some directories 
% to the search path;
for i = 1:length(Paths)
addpath(genpath(Paths{i})); % 
end

% make a subject folder;
mkdir([OutDir '/HeadModel/']);
cd([OutDir '/HeadModel/']);

% if T2w image
% is available;
if ~isempty(T2w)
    
    % copy the T1w & T2w images to Head Model directory;
    system(['cp ' T1w ' ' OutDir '/HeadModel/T1w.nii.gz']); 
    system(['cp ' T2w ' ' OutDir '/HeadModel/T2w.nii.gz']); 

    % construct a tetrahedral headmesh using headreco using both the T1w and T2w images;
    system(['charm ' Subject ' T1w.nii.gz T2w.nii.gz --forceqform']);
    
else
    
    % copy the T1w image to Head Model directory;
    system(['cp ' T1w ' ' OutDir '/HeadModel/T1w.nii.gz']); 

    % construct a tetrahedral headmesh using headreco using only the T1w image;
    system(['charm ' Subject ' T1w.nii.gz --forceqform']);
    
end

% extract the skin tissue and "background" compartment; 
system(['mri_binarize --i ' OutDir '/HeadModel/m2m_' Subject '/final_tissues.nii.gz --match 5 --o ' OutDir '/HeadModel/m2m_' Subject '/skin.nii.gz > /dev/null 2>&1']);
system(['mri_binarize --i ' OutDir '/HeadModel/m2m_' Subject '/segmentation/labeling.nii.gz --match 517 0 --o ' OutDir '/HeadModel/m2m_' Subject '/background.nii.gz --inv > /dev/null 2>&1 ']);
system(['fslmaths ' OutDir '/HeadModel/m2m_' Subject '/skin.nii.gz -add ' OutDir '/HeadModel/m2m_' Subject '/background.nii.gz -bin ' OutDir '/HeadModel/m2m_' Subject '/skin.nii.gz > /dev/null 2>&1']);

% remove "edge" effects 
% (otherwise we can end up with holes in our skin mesh)
nii = niftiread([OutDir '/HeadModel/m2m_' Subject '/skin.nii.gz']);
dims = size(nii); % nii dimensions
nii([1 dims(1)],:,:) = 0; 
nii(:,[1 dims(2)],:) = 0; 
nii(:,:,[1 dims(3)]) = 0;
nii_info = niftiinfo([OutDir '/HeadModel/m2m_' Subject '/skin.nii.gz']);
niftiwrite(nii,[OutDir '/HeadModel/m2m_' Subject '/skin_noedges'],nii_info);
system(['gzip ' OutDir '/HeadModel/m2m_' Subject '/skin_noedges.nii -f']);

% create skin surface mesh;
system(['mri_tessellate -n ' OutDir '/HeadModel/m2m_' Subject '/skin_noedges.nii.gz 1 ' OutDir '/HeadModel/m2m_' Subject '/skin.orig > /dev/null 2>&1']);
system(['mris_convert ' OutDir '/HeadModel/m2m_' Subject '/skin.orig ' OutDir '/HeadModel/m2m_' Subject '/Skin.surf.gii > /dev/null 2>&1']);
system(['wb_command -surface-smoothing ' OutDir '/HeadModel/m2m_' Subject '/Skin.surf.gii ' num2str(skinSmoothingStrength) ' ' num2str(skinSmoothingIterations) ' ' OutDir '/HeadModel/m2m_' Subject '/Skin.surf.gii > /dev/null 2>&1']);
system(['wb_command -set-structure ' OutDir '/HeadModel/m2m_' Subject '/Skin.surf.gii CORTEX_LEFT -surface-type RECONSTRUCTION > /dev/null 2>&1']);

% now, prepare some files that can
% be imported into BrainSight later on if desired,

% create a .stl file as well
system(['mris_convert ' OutDir '/HeadModel/m2m_' Subject '/Skin.surf.gii '...
OutDir '/HeadModel/m2m_' Subject '/Skin.stl > /dev/null 2>&1']);

% load the head model for this subject;
M = mesh_load_gmsh4([OutDir '/HeadModel/m2m_' Subject '/' Subject '.msh']);
S = mesh_extract_regions(M,'region_idx',[2 1002]); % 2 == GM.

% convert to .surf.gii
G = gifti; % preallocate;
G.mat = eye(4); % identity matrix
G.vertices = single(S.nodes); % vertices
G.faces = int32(S.triangles); % edges
save(G,[OutDir '/HeadModel/m2m_' Subject '/GrayMatter.surf.gii']);

% apply spatial smoothing and write out as a .stl file;
system(['wb_command -surface-smoothing ' OutDir '/HeadModel/m2m_' Subject '/GrayMatter.surf.gii ' num2str(grayMatterSmoothingStrength) ' ' num2str(grayMatterSmoothingIterations) ' ' OutDir '/HeadModel/m2m_' Subject '/GrayMatter.surf.gii > /dev/null 2>&1']);
system(['mris_convert ' OutDir '/HeadModel/m2m_' Subject '/GrayMatter.surf.gii ' OutDir '/HeadModel/m2m_' Subject '/GrayMatter.stl']);

end

function out = i_get_opt(S, field, defaultVal)
if isfield(S, field) && ~isempty(S.(field))
    out = S.(field);
else
    out = defaultVal;
end
end
