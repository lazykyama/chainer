import unittest

import numpy

import chainer
from chainer import backend
from chainer.backends import cuda
from chainer import gradient_check
from chainer import links
from chainer import testing
from chainer.testing import attr


@testing.parameterize(*(testing.product({
    'shape': [(1, 4, 5, 3), (5, 4, 7), (3, 20)],
    'groups': [1, 2, 4],
    'dtype': [numpy.float16, numpy.float32, numpy.float64,
              chainer.mixed16],
})))
class GroupNormalizationTest(unittest.TestCase):

    def setUp(self):
        with chainer.using_config('dtype', self.dtype):
            self.link = links.GroupNormalization(self.groups)
        self.link.cleargrads()

        self.x, = self.generate_inputs()
        self.gy = numpy.random.uniform(-1, 1, self.shape).astype(self.dtype)

        if self.dtype in (numpy.float16, chainer.mixed16):
            self.check_forward_options = {'atol': 1e-2, 'rtol': 1e-1}
            self.check_backward_options = {'atol': 5e-1, 'rtol': 1e-1}
        else:
            self.check_forward_options = {'atol': 1e-4, 'rtol': 1e-3}
            self.check_backward_options = {'atol': 1e-3, 'rtol': 1e-2}

    def generate_inputs(self):
        shape = self.shape

        # sample x such that x.std >= min_std
        min_std = 0.02
        retry = 0
        while True:
            x = numpy.random.uniform(-1, 1, shape).astype(self.dtype)
            x_groups = x.reshape(shape[0], self.groups, -1)
            if x_groups.std(axis=2).min() >= min_std:
                break
            retry += 1
            assert retry <= 20, 'Too many retries to generate inputs'

        return x,

    def check_forward(self, x_data):
        y = self.link(x_data)
        self.assertEqual(y.data.dtype, self.dtype)

        # Verify that forward isn't be affected by batch size
        if self.shape[0] > 1:
            xp = backend.get_array_module(x_data)
            y_one_each = chainer.functions.concat(
                [self.link(xp.expand_dims(one_x, axis=0))
                 for one_x in x_data], axis=0)
            testing.assert_allclose(
                y.data, y_one_each.data, **self.check_forward_options)

    def test_forward_cpu(self):
        self.check_forward(self.x)

    @attr.gpu
    def test_forward_gpu(self):
        self.link.to_gpu()
        self.check_forward(cuda.to_gpu(self.x))

    @attr.cudnn
    def test_forward_gpu_without_cudnn(self):
        self.link.use_cudnn = False
        self.test_forward_gpu()

    @attr.multi_gpu(2)
    def test_forward_multi_gpu(self):
        with cuda.get_device_from_id(1):
            self.link.to_gpu()
            x = cuda.to_gpu(self.x)
        with cuda.get_device_from_id(0):
            self.check_forward(x)

    def check_backward(self, x_data, y_grad):
        gradient_check.check_backward(
            self.link, x_data, y_grad,
            (self.link.gamma, self.link.beta),
            eps=2e-2, **self.check_backward_options)

    def test_backward_cpu(self):
        self.link(numpy.zeros(self.shape, dtype=self.dtype))
        self.check_backward(self.x, self.gy)

    @attr.gpu
    def test_backward_gpu(self):
        self.link.to_gpu()
        self.link(cuda.cupy.zeros(self.shape, dtype=self.dtype))
        self.check_backward(cuda.to_gpu(self.x), cuda.to_gpu(self.gy))

    @attr.cudnn
    def test_backward_gpu_without_cudnn(self):
        self.link.use_cudnn = False
        self.link(numpy.zeros(self.shape, dtype=self.dtype))
        self.test_backward_gpu()


@testing.parameterize(*testing.product({
    'size': [3, 30],
    'groups': [1, 3],
    'dtype': [numpy.float16, numpy.float32, numpy.float64,
              chainer.mixed16],
}))
class TestInitialize(unittest.TestCase):

    def setUp(self):
        self.lowprec_dtype = chainer.get_dtype(self.dtype)
        highprec_dtype = chainer.get_dtype(
            self.dtype, map_mixed16=numpy.float32)

        self.initial_gamma = numpy.random.uniform(-1, 1, self.size)
        self.initial_gamma = self.initial_gamma.astype(highprec_dtype)
        self.initial_beta = numpy.random.uniform(-1, 1, self.size)
        self.initial_beta = self.initial_beta.astype(highprec_dtype)
        self.link = links.GroupNormalization(self.groups,
                                             initial_gamma=self.initial_gamma,
                                             initial_beta=self.initial_beta)
        self.shape = (1, self.size, 1)

    def test_initialize_cpu(self):
        with chainer.using_config('dtype', self.dtype):
            self.link(numpy.zeros(self.shape, dtype=self.lowprec_dtype))
        testing.assert_allclose(self.initial_gamma, self.link.gamma.data)
        testing.assert_allclose(self.initial_beta, self.link.beta.data)

    @attr.gpu
    def test_initialize_gpu(self):
        self.link.to_gpu()
        with chainer.using_config('dtype', self.dtype):
            self.link(cuda.cupy.zeros(self.shape, dtype=self.lowprec_dtype))
        testing.assert_allclose(self.initial_gamma, self.link.gamma.data)
        testing.assert_allclose(self.initial_beta, self.link.beta.data)


@testing.parameterize(*testing.product({
    'size': [3, 30],
    'groups': [1, 3],
    'dtype': [numpy.float16, numpy.float32, numpy.float64,
              chainer.mixed16],
}))
class TestDefaultInitializer(unittest.TestCase):

    def setUp(self):
        self.lowprec_dtype = chainer.get_dtype(self.dtype)
        self.highprec_dtype = chainer.get_dtype(
            self.dtype, map_mixed16=numpy.float32)

        self.size = 3
        with chainer.using_config('dtype', self.dtype):
            self.link = links.GroupNormalization(self.groups)
        self.shape = (1, self.size, 1)

    def test_initialize_cpu(self):
        self.link(numpy.zeros(self.shape, dtype=self.lowprec_dtype))

        testing.assert_allclose(numpy.ones(self.size), self.link.gamma.data)
        self.assertEqual(self.link.gamma.dtype, self.highprec_dtype)

        testing.assert_allclose(
            numpy.zeros(self.size), self.link.beta.data)
        self.assertEqual(self.link.beta.dtype, self.highprec_dtype)

    @attr.gpu
    def test_initialize_gpu(self):
        self.link.to_gpu()
        self.link(cuda.cupy.zeros(self.shape, dtype=self.lowprec_dtype))

        testing.assert_allclose(numpy.ones(self.size), self.link.gamma.data)
        self.assertEqual(self.link.gamma.dtype, self.highprec_dtype)

        testing.assert_allclose(
            numpy.zeros(self.size), self.link.beta.data)
        self.assertEqual(self.link.beta.dtype, self.highprec_dtype)


@testing.parameterize(*testing.product({
    'shape': [(2,), ()],
    'dtype': [numpy.float16, numpy.float32, numpy.float64],
}))
class TestInvalidInput(unittest.TestCase):

    def setUp(self):
        self.link = links.GroupNormalization(groups=3)

    def test_invalid_shape_cpu(self):
        with self.assertRaises(ValueError):
            self.link(chainer.Variable(
                numpy.zeros(self.shape, dtype=self.dtype)))

    @attr.gpu
    def test_invalid_shape_gpu(self):
        self.link.to_gpu()
        with self.assertRaises(ValueError):
            self.link(
                chainer.Variable(
                    cuda.cupy.zeros(self.shape, dtype=self.dtype)))


@testing.parameterize(*testing.product({
    'dtype': [numpy.float16, numpy.float32, numpy.float64],
}))
class TestInvalidInitialize(unittest.TestCase):

    def setUp(self):
        shape = (2, 5, 2)
        self.x = chainer.Variable(numpy.zeros(shape, dtype=self.dtype))

    def test_invalid_groups(self):
        self.link = links.GroupNormalization(groups=3)
        with self.assertRaises(ValueError):
            self.link(self.x)

    def test_invalid_type_groups(self):
        self.link = links.GroupNormalization(groups=3.5)
        with self.assertRaises(TypeError):
            self.link(self.x)


testing.run_module(__name__, __file__)
