import logging
import os
import os.path as op

import nibabel as nb
from nipype.interfaces.ants.base import ANTSCommand, ANTSCommandInputSpec
from nipype.interfaces.base import (
    BaseInterfaceInputSpec,
    CommandLine,
    CommandLineInputSpec,
    File,
    InputMultiObject,
    OutputMultiObject,
    SimpleInterface,
    TraitedSpec,
    isdefined,
    traits,
)
from nipype.utils.filemanip import split_filename

LOGGER = logging.getLogger('nipype.interface')


# Step 1 from DSI Studio, importing DICOM files or nifti
class MultivariateTemplateConstruction2InputSpec(ANTSCommandInputSpec):
    dimension = traits.Enum(2, 3, 4, default=3, usedefault=True, argstr='-d %d')
    input_file = File(desc='txt or csv file with images', exists=True, position=-1)
    input_images = InputMultiObject(
        traits.Either(File(exists=True), InputMultiObject(File(exists=True))),
        desc='list of images or lists of images',
        xor=('input_file',),
        argstr='%s',
        position=-1,
        copyfile=False,
    )
    image_statistic = traits.Enum(
        0,
        1,
        2,
        default=1,
        usedefault=True,
        desc='statistic used to summarize '
        'images. 0=mean, 1= mean of normalized intensities, 2=median',
    )
    iteration_limit = traits.Int(
        4, usedefault=True, argstr='-i %d', desc='maximum number of iterations'
    )
    backup_images = traits.Bool(False, argstr='-b %d')
    parallel_control = traits.Enum(
        0,
        1,
        2,
        3,
        4,
        5,
        desc='Control for parallel computation '
        '0 = run serially, '
        '1 = SGE qsub, '
        '2 = use PEXEC (localhost), '
        '3 = Apple XGrid, '
        '4 = PBS qsub, '
        '5 = SLURM',
        argstr='-c %d',
        usedefault=True,
        hash_files=False,
    )
    num_cores = traits.Int(default=1, usedefault=True, argstr='-j %d', hash_files=False)
    num_modalities = traits.Int(
        1,
        usedefault=True,
        desc='Number of modalities used '
        'to construct the template (default 1):  For example, '
        'if one wanted to create a multimodal template consisting of T1,T2,and FA '
        'components ("-k 3")',
        argstr='-k %d',
    )
    modality_weights = traits.List([1], usedefault=True)
    n4_bias_correct = traits.Bool(True, usedefault=True, argstr='-n %d')
    metric = traits.Str('CC', usedefault=True, argstr='-m %s', mandatory=True)
    transform = traits.Enum(
        'BSplineSyN', 'SyN', 'Affine', usedefault=True, argstr='-t %s', mandatory=True
    )
    output_prefix = traits.Str('antsBTP')
    gradient_step = traits.Float(0.25, usedefault=True, mandatory=True, argstr='-g %.3f')
    use_full_affine = traits.Bool(False, usedefault=True, argstr='-y %d')
    usefloat = traits.Bool(True, argstr='-e %d', usedefault=True)


class MultivariateTemplateConstruction2OutputSpec(TraitedSpec):
    templates = OutputMultiObject(File(exists=True), mandatory=True)
    forward_transforms = OutputMultiObject(OutputMultiObject(File(exists=True)), mandatory=True)
    reverse_transforms = OutputMultiObject(OutputMultiObject(File(exists=True)), mandatory=True)
    iteration_templates = OutputMultiObject(File(exists=True))


class MultivariateTemplateConstruction2(ANTSCommand):
    input_spec = MultivariateTemplateConstruction2InputSpec
    output_spec = MultivariateTemplateConstruction2OutputSpec
    _cmd = 'antsMultivariateTemplateConstruction2.sh '

    def _format_arg(self, opt, spec, val):
        if opt == 'input_images':
            return ' '.join([op.split(fname)[1] for fname in val])
        if opt == 'modality_weights':
            return 'x'.join([f'{weight:.3f}' for weight in val])
        return super()._format_arg(opt, spec, val)

    def _list_outputs(self):
        if isdefined(self.inputs.input_file):
            raise NotImplementedError()
        forward_transforms = []
        reverse_transforms = []
        if isdefined(self.inputs.output_prefix):
            prefix = self.inputs.output_prefix
        else:
            prefix = 'antsBTP'
        cwd = os.getcwd()
        for num, input_image in enumerate(self.inputs.input_images):
            if isinstance(input_image, list):
                input_image = input_image[0]
            path, fname, ext = split_filename(input_image)
            affine = f'{cwd}/{prefix}{fname}{num}0GenericAffine.mat'
            warp = f'{cwd}/{prefix}{fname}{num}1Warp.nii.gz'
            inv_warp = f'{cwd}/{prefix}{fname}{num}1InverseWarp.nii.gz'
            forward_transforms.append([affine, warp])
            reverse_transforms.append([inv_warp, affine])

        templates = [
            f'{cwd}/{prefix}template{tnum}.nii.gz' for tnum in range(self.inputs.num_modalities)
        ]
        outputs = self.output_spec().get()
        outputs['forward_transforms'] = forward_transforms
        outputs['reverse_transforms'] = reverse_transforms
        outputs['templates'] = templates

        return outputs

    def _num_threads_update(self):
        # Parallelization is controlled by ants pexec
        self.inputs.environ.update({'ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS': '1', 'NSLOTS': '1'})


class ImageMathInputSpec(BaseInterfaceInputSpec):
    in_file = File(exists=True, mandatory=True, position=3, argstr='%s')
    dimension = traits.Enum(3, 2, 4, usedefault=True, argstr='%d', position=0)
    out_file = File(argstr='%s', genfile=True, position=1)
    operation = traits.Str(argstr='%s', position=2)
    secondary_arg = traits.Str('', argstr='%s')
    secondary_file = File(argstr='%s')


class ImageMathOutputSpec(TraitedSpec):
    out_file = File(exists=True)


class ImageMath(CommandLine):
    input_spec = ImageMathInputSpec
    output_spec = ImageMathOutputSpec
    _cmd = 'ImageMath'

    def _gen_filename(self, name):
        if name == 'out_file':
            output = self.inputs.out_file
            if not isdefined(output):
                _, fname, ext = split_filename(self.inputs.in_file)
                output = fname + '_' + self.inputs.operation + ext
            return output
        return None

    def _list_outputs(self):
        outputs = self.output_spec().get()
        outputs['out_file'] = op.abspath(self._gen_filename('out_file'))
        return outputs


class _ConvertTransformFileInputSpec(CommandLineInputSpec):
    dimension = traits.Enum((3, 2), default=3, usedefault=True, argstr='%d', position=0)
    in_transform = traits.File(exists=True, argstr='%s', mandatory=True, position=1)
    out_transform = traits.File(
        argstr='%s',
        name_source='in_transform',
        name_template='%s.txt',
        keep_extension=False,
        position=2,
    )


class _ConvertTransformFileOutputSpec(TraitedSpec):
    out_transform = traits.File(exists=True)


class ConvertTransformFile(CommandLine):
    _cmd = 'ConvertTransformFile'
    input_spec = _ConvertTransformFileInputSpec
    output_spec = _ConvertTransformFileOutputSpec


class _GetImageTypeInputSpec(BaseInterfaceInputSpec):
    image = File(exists=True, mandatory=True)


class _GetImageTypeOutputSpec(TraitedSpec):
    image_type = traits.Enum(0, 1, 2, 3)


class GetImageType(SimpleInterface):
    """Use to determine what to send to --input-image-type."""

    input_spec = _GetImageTypeInputSpec
    output_spec = _GetImageTypeOutputSpec

    def _run_interface(self, runtime):
        img = nb.load(self.inputs.image)
        if img.ndim == 4:
            self._results['image_type'] = 3
        else:
            self._results['image_type'] = 0
        return runtime
