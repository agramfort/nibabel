from __future__ import division, print_function, absolute_import

import warnings
import numpy as np
import getpass
import time

from ..externals import OrderedDict
from ..externals.six.moves import xrange
from ..openers import Opener


def _fread3(fobj):
    """Read a 3-byte int from an open binary file object

    Parameters
    ----------
    fobj : file
        File descriptor

    Returns
    -------
    n : int
        A 3 byte int
    """
    b1, b2, b3 = np.fromfile(fobj, ">u1", 3)
    return (b1 << 16) + (b2 << 8) + b3


def _fread3_many(fobj, n):
    """Read 3-byte ints from an open binary file object.

    Parameters
    ----------
    fobj : file
        File descriptor

    Returns
    -------
    out : 1D array
        An array of 3 byte int
    """
    b1, b2, b3 = np.fromfile(fobj, ">u1", 3 * n).reshape(-1,
                                                         3).astype(np.int).T
    return (b1 << 16) + (b2 << 8) + b3


def _read_volume_info(extra):
    volume_info = OrderedDict()

    if extra is None:  # we pass b'' when user does not request info
        pass
    elif len(extra) == 0:
        warnings.warn('No volume information contained in the file')
    elif extra[:4] != b'\x00\x00\x00\x14':
        warnings.warn("Unknown extension code for volume info: %r" % extra[:4])
    else:
        try:
            for line in extra[4:].split(b'\n'):
                if len(line) == 0:
                    continue
                key, val = map(bytes.strip, line.split(b'=', 1))
                key = key.decode('utf-8')
                if key in ('voxelsize', 'xras', 'yras', 'zras', 'cras'):
                    val = np.fromstring(val, sep=' ')
                    val = val.astype(np.float)
                elif key == 'volume':
                    val = np.fromstring(val, sep=' ', dtype=np.uint)
                    val = val.astype(np.int)
                volume_info[key] = val
        except Exception as exp:
            raise ValueError("Error parsing volume info: %s" % str(exp))
        if len(volume_info) == 0:
            warnings.warn("Volume geometry info is either "
                          "not contained or not valid.")
    return volume_info


def read_geometry(filepath, read_metadata=False, read_stamp=False):
    """Read a triangular format Freesurfer surface mesh.

    Parameters
    ----------
    filepath : str
        Path to surface file
    read_metadata : bool
        Read metadata as key-value pairs
    read_stamp : bool
        Return the comment from the file

    Returns
    -------
    coords : numpy array
        nvtx x 3 array of vertex (x, y, z) coordinates
    faces : numpy array
        nfaces x 3 array of defining mesh triangles
    volume_info : OrderedDict
        If read_metadata is true, key-value pairs found in the geometry file
    create_stamp : str
        If read_stamp is true, the comment added by the program that saved
        the file
    """
    volume_info = None

    with open(filepath, "rb") as fobj:
        magic = _fread3(fobj)
        if magic == 16777215:  # Quad file
            nvert = _fread3(fobj)
            nquad = _fread3(fobj)
            coords = np.fromfile(fobj, ">i2", nvert * 3).astype(np.float)
            coords = coords.reshape(-1, 3) / 100.0
            quads = _fread3_many(fobj, nquad * 4)
            quads = quads.reshape(nquad, 4)
            #
            #   Face splitting follows
            #
            faces = np.zeros((2 * nquad, 3), dtype=np.int)
            nface = 0
            for quad in quads:
                if (quad[0] % 2) == 0:
                    faces[nface] = quad[0], quad[1], quad[3]
                    nface += 1
                    faces[nface] = quad[2], quad[3], quad[1]
                    nface += 1
                else:
                    faces[nface] = quad[0], quad[1], quad[2]
                    nface += 1
                    faces[nface] = quad[0], quad[2], quad[3]
                    nface += 1

        elif magic == 16777214:  # Triangle file
            create_stamp = fobj.readline().rstrip(b'\n').decode('utf-8')
            fobj.readline()
            vnum = np.fromfile(fobj, ">i4", 1)[0]
            fnum = np.fromfile(fobj, ">i4", 1)[0]
            coords = np.fromfile(fobj, ">f4", vnum * 3).reshape(vnum, 3)
            faces = np.fromfile(fobj, ">i4", fnum * 3).reshape(fnum, 3)
            extra = fobj.read() if read_metadata else None
            volume_info = _read_volume_info(extra)
        else:
            raise ValueError("File does not appear to be a Freesurfer surface")

    coords = coords.astype(np.float)  # XXX: due to mayavi bug on mac 32bits

    ret = (coords, faces)
    if read_metadata:
        ret += (volume_info,)
    if read_stamp:
        ret += (create_stamp,)

    return ret


def write_geometry(filepath, coords, faces, create_stamp=None,
                   volume_info=None):
    """Write a triangular format Freesurfer surface mesh.

    Parameters
    ----------
    filepath : str
        Path to surface file
    coords : numpy array
        nvtx x 3 array of vertex (x, y, z) coordinates
    faces : numpy array
        nfaces x 3 array of defining mesh triangles
    create_stamp : str
        User/time stamp (default: "created by <user> on <ctime>")
    volume_info : dict-like or None
        Key-value pairs to encode at the end of the file
    """
    magic_bytes = np.array([255, 255, 254], dtype=np.uint8)

    if create_stamp is None:
        create_stamp = "created by %s on %s" % (getpass.getuser(),
                                                time.ctime())

    postlude = b''
    if volume_info is not None and len(volume_info) > 0:
        postlude = [b'\x00\x00\x00\x14']
        for key, val in volume_info.items():
            if key in ('voxelsize', 'xras', 'yras', 'zras', 'cras'):
                val = '{0:.4f} {1:.4f} {2:.4f}'.format(*val)
            elif key == 'volume':
                val = '{0:d} {1:d} {2:d}'.format(*val)
            key = key.ljust(6)
            postlude.append('{0} = {1}'.format(key, val).encode('utf-8'))
        postlude = b'\n'.join(postlude)

    with open(filepath, 'wb') as fobj:
        magic_bytes.tofile(fobj)
        fobj.write(("%s\n\n" % create_stamp).encode('utf-8'))

        np.array([coords.shape[0], faces.shape[0]], dtype='>i4').tofile(fobj)

        # Coerce types, just to be safe
        coords.astype('>f4').reshape(-1).tofile(fobj)
        faces.astype('>i4').reshape(-1).tofile(fobj)

        # Add volume info, if given
        fobj.write(postlude)


def read_morph_data(filepath):
    """Read a Freesurfer morphometry data file.

    This function reads in what Freesurfer internally calls "curv" file types,
    (e.g. ?h. curv, ?h.thickness), but as that has the potential to cause
    confusion where "curv" also refers to the surface curvature values,
    we refer to these files as "morphometry" files with PySurfer.

    Parameters
    ----------
    filepath : str
        Path to morphometry file

    Returns
    -------
    curv : numpy array
        Vector representation of surface morpometry values

    """
    with open(filepath, "rb") as fobj:
        magic = _fread3(fobj)
        if magic == 16777215:
            vnum = np.fromfile(fobj, ">i4", 3)[0]
            curv = np.fromfile(fobj, ">f4", vnum)
        else:
            vnum = magic
            _fread3(fobj)
            curv = np.fromfile(fobj, ">i2", vnum) / 100
    return curv


def write_morph_data(file_like, values, fnum=0):
    """Write Freesurfer morphometry data `values` to file-like `file_like`

    Equivalent to FreeSurfer's `write_curv.m`_

    See also:
    http://www.grahamwideman.com/gw/brain/fs/surfacefileformats.htm#CurvNew

    .. _write_curv.m: \
    https://github.com/neurodebian/freesurfer/blob/debian-sloppy/matlab/write_curv.m

    Parameters
    ----------
    file_like : file-like
        String containing path of file to be written, or file-like object, open
        in binary write (`'wb'` mode, implementing the `write` method)
    values : array-like
        Surface morphometry values

        Shape must be (N,), (N, 1), (1, N) or (N, 1, 1)
    fnum : int, optional
        Number of faces in the associated surface
    """
    magic_bytes = np.array([255, 255, 255], dtype=np.uint8)

    vector = np.asarray(values)
    vnum = np.prod(vector.shape)
    if vector.shape not in ((vnum,), (vnum, 1), (1, vnum), (vnum, 1, 1)):
        raise ValueError("Invalid shape: argument values must be a vector")

    i4info = np.iinfo('i4')
    if vnum > i4info.max:
        raise ValueError("Too many values for morphometry file")
    if not i4info.min <= fnum <= i4info.max:
        raise ValueError("Argument fnum must be between {0} and {1}".format(
                         i4info.min, i4info.max))

    with Opener(file_like, 'wb') as fobj:
        fobj.write(magic_bytes)

        # vertex count, face count (unused), vals per vertex (only 1 supported)
        fobj.write(np.array([vnum, fnum, 1], dtype='>i4'))

        fobj.write(vector.astype('>f4'))


def read_annot(filepath, orig_ids=False):
    """Read in a Freesurfer annotation from a .annot file.

    Parameters
    ----------
    filepath : str
        Path to annotation file.
    orig_ids : bool
        Whether to return the vertex ids as stored in the annotation
        file or the positional colortable ids. With orig_ids=False
        vertices with no id have an id set to -1.

    Returns
    -------
    labels : ndarray, shape (n_vertices,)
        Annotation id at each vertex. If a vertex does not belong
        to any label and orig_ids=False, its id will be set to -1.
    ctab : ndarray, shape (n_labels, 5)
        RGBA + label id colortable array.
    names : list of str
        The names of the labels. The length of the list is n_labels.
    """
    with open(filepath, "rb") as fobj:
        dt = ">i4"
        vnum = np.fromfile(fobj, dt, 1)[0]
        data = np.fromfile(fobj, dt, vnum * 2).reshape(vnum, 2)
        labels = data[:, 1]

        ctab_exists = np.fromfile(fobj, dt, 1)[0]
        if not ctab_exists:
            raise Exception('Color table not found in annotation file')
        n_entries = np.fromfile(fobj, dt, 1)[0]
        if n_entries > 0:
            length = np.fromfile(fobj, dt, 1)[0]
            orig_tab = np.fromfile(fobj, '>c', length)
            orig_tab = orig_tab[:-1]

            names = list()
            ctab = np.zeros((n_entries, 5), np.int)
            for i in xrange(n_entries):
                name_length = np.fromfile(fobj, dt, 1)[0]
                name = np.fromfile(fobj, "|S%d" % name_length, 1)[0]
                names.append(name)
                ctab[i, :4] = np.fromfile(fobj, dt, 4)
                ctab[i, 4] = (ctab[i, 0] + ctab[i, 1] * (2 ** 8) +
                              ctab[i, 2] * (2 ** 16) +
                              ctab[i, 3] * (2 ** 24))
        else:
            ctab_version = -n_entries
            if ctab_version != 2:
                raise Exception('Color table version not supported')
            n_entries = np.fromfile(fobj, dt, 1)[0]
            ctab = np.zeros((n_entries, 5), np.int)
            length = np.fromfile(fobj, dt, 1)[0]
            np.fromfile(fobj, "|S%d" % length, 1)[0]  # Orig table path
            entries_to_read = np.fromfile(fobj, dt, 1)[0]
            names = list()
            for i in xrange(entries_to_read):
                np.fromfile(fobj, dt, 1)[0]  # Structure
                name_length = np.fromfile(fobj, dt, 1)[0]
                name = np.fromfile(fobj, "|S%d" % name_length, 1)[0]
                names.append(name)
                ctab[i, :4] = np.fromfile(fobj, dt, 4)
                ctab[i, 4] = (ctab[i, 0] + ctab[i, 1] * (2 ** 8) +
                              ctab[i, 2] * (2 ** 16))
        ctab[:, 3] = 255

    labels = labels.astype(np.int)

    if not orig_ids:
        ord = np.argsort(ctab[:, -1])
        mask = labels != 0
        labels[~mask] = -1
        labels[mask] = ord[np.searchsorted(ctab[ord, -1], labels[mask])]
    return labels, ctab, names


def write_annot(filepath, labels, ctab, names):
    """Write out a Freesurfer annotation file.

    See:
    https://surfer.nmr.mgh.harvard.edu/fswiki/LabelsClutsAnnotationFiles#Annotation

    Parameters
    ----------
    filepath : str
        Path to annotation file to be written
    labels : ndarray, shape (n_vertices,)
        Annotation id at each vertex.
    ctab : ndarray, shape (n_labels, 5)
        RGBA + label id colortable array.
    names : list of str
        The names of the labels. The length of the list is n_labels.
    """
    with open(filepath, "wb") as fobj:
        dt = ">i4"
        vnum = len(labels)

        def write(num, dtype=dt):
            np.array([num]).astype(dtype).tofile(fobj)

        def write_string(s):
            write(len(s))
            write(s, dtype='|S%d' % len(s))

        # vtxct
        write(vnum)

        # convert labels into coded CLUT values
        clut_labels = ctab[:, -1][labels]
        clut_labels[np.where(labels == -1)] = 0

        # vno, label
        data = np.vstack((np.array(range(vnum)),
                          clut_labels)).T.astype(dt)
        data.tofile(fobj)

        # tag
        write(1)

        # ctabversion
        write(-2)

        # maxstruc
        write(np.max(labels) + 1)

        # File of LUT is unknown.
        write_string('NOFILE')

        # num_entries
        write(ctab.shape[0])

        for ind, (clu, name) in enumerate(zip(ctab, names)):
            write(ind)
            write_string(name)
            for val in clu[:-1]:
                write(val)


def read_label(filepath, read_scalars=False):
    """Load in a Freesurfer .label file.

    Parameters
    ----------
    filepath : str
        Path to label file
    read_scalars : bool
        If true, read and return scalars associated with each vertex

    Returns
    -------
    label_array : numpy array
        Array with indices of vertices included in label
    scalar_array : numpy array (floats)
        If read_scalars is True, array of scalar data for each vertex

    """
    label_array = np.loadtxt(filepath, dtype=np.int, skiprows=2, usecols=[0])
    if read_scalars:
        scalar_array = np.loadtxt(filepath, skiprows=2, usecols=[-1])
        return label_array, scalar_array
    return label_array
