#!/usr/bin/env python3
"""Extract exterior CHARM skin, excluding internal air-cavity interfaces."""

import argparse
import hashlib
import json
from pathlib import Path

import nibabel as nib
import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def partition_external_scalp(mesh):
    # SimNIBS combines crop criteria with OR, not AND. Apply them separately.
    scalp = mesh.crop_mesh(elm_type=2).crop_mesh(tags=[1005])
    if not scalp.nodes.nr or not scalp.elm.nr:
        raise ValueError('CHARM mesh contains no scalp triangles with tag 1005')
    if np.any(scalp.elm.elm_type != 2) or np.any(scalp.elm.tag1 != 1005):
        raise ValueError('Scalp extraction retained non-scalp elements')
    # Tag 1005 also contains nasal/cavity-facing skin. Partition the full mesh:
    # this API returns one-based face IDs in the original mesh, not in scalp.
    if not np.any(mesh.elm.elm_type == 4):
        raise ValueError('Exterior skin extraction requires the complete CHARM volume mesh')
    _, _, inner_faces, outer_faces = mesh.partition_skin_surface(
        assume_single_outside_component=True)
    if not len(outer_faces):
        raise ValueError('SimNIBS found no exterior skin faces')
    exterior = mesh.crop_mesh(elements=outer_faces)
    if np.any(exterior.elm.elm_type != 2) or np.any(exterior.elm.tag1 != 1005):
        raise ValueError('Exterior skin partition returned non-skin elements')
    if len(exterior.elm.connected_components()) != 1:
        raise ValueError('Exterior skin must form one connected surface')
    return exterior, {
        'surface_scope': 'exterior_skin',
        'partition_method': 'simnibs.Msh.partition_skin_surface',
        'assume_single_outside_component': True,
        'original_skin_triangles': int(scalp.elm.nr),
        'excluded_internal_skin_triangles': int(len(inner_faces)),
    }


def extract_scalp(mesh):
    return partition_external_scalp(mesh)[0]


def validate_surface(path, scalp):
    image = nib.load(str(path))
    points = image.get_arrays_from_intent('NIFTI_INTENT_POINTSET')[0].data
    faces = image.get_arrays_from_intent('NIFTI_INTENT_TRIANGLE')[0].data
    expected_faces = scalp.elm.node_number_list[:, :3] - 1
    if (points.shape != scalp.nodes.node_coord.shape or
            not np.allclose(points, scalp.nodes.node_coord, atol=2e-5, rtol=0) or
            not np.array_equal(faces, expected_faces)):
        raise ValueError(
            'Scalp surface does not match exterior-only CHARM skin triangles. '
            'Do not reuse grids derived from it; regenerate scalp and downstream stages.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    # Report helpers read saved geometry without requiring the FEM environment.
    from simnibs import mesh_io

    mesh = mesh_io.read_msh(str(source))
    scalp, partition = partition_external_scalp(mesh)
    if not args.validate_only:
        if output.exists():
            raise FileExistsError(f'Refusing to replace an existing scalp surface: {output}')
        output.parent.mkdir(parents=True, exist_ok=True)
        mesh_io.write_gifti_surface(scalp, str(output))
    validate_surface(output, scalp)
    proof = {
        'schema_version': 'tans-scalp-extraction-v3',
        'status': 'validated', 'element_type': 2, 'element_tag': 1005,
        **partition,
        'source_head_mesh': str(source), 'source_head_mesh_sha256': sha256(source),
        'surface': str(output), 'surface_sha256': sha256(output),
        'vertices': int(scalp.nodes.nr), 'triangles': int(scalp.elm.nr),
    }
    output.with_suffix(output.suffix + '.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(f"validated {output}: {scalp.nodes.nr} scalp vertices, {scalp.elm.nr} triangles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
