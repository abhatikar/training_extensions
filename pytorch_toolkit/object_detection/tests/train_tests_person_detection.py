# Copyright (C) 2020 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.

import os

from common.test_case import create_object_detection_test_case

kwargs = dict(
    problem_name='person-detection',
    ann_file=os.path.dirname(__file__) + '/../../../data/airport/annotation_person_train.json',
    img_root=os.path.dirname(__file__) + '/../../../data/airport/train',
)


class PersonDetection0200TestCase(
        create_object_detection_test_case(
            model_name='person-detection-0200',
            **kwargs,
        )
):
    """ Test case for person-detection-0200 model. """


class PersonDetection0201TestCase(
        create_object_detection_test_case(
            model_name='person-detection-0201',
            **kwargs,
        )
):
    """ Test case for person-detection-0201 model. """


class PersonDetection0202TestCase(
        create_object_detection_test_case(
            model_name='person-detection-0202',
            **kwargs,
        )
):
    """ Test case for person-detection-0202 model. """


class PersonDetection0203TestCase(
        create_object_detection_test_case(
            model_name='person-detection-0203',
            **kwargs,
        )
):
    """ Test case for person-detection-0203 model. """

