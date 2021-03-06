import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.colors import Normalize

import tensorflow as tf
from tensorboard import summary as summary_lib

import numpy as np
from PIL import Image
import cv2
import io
import time


def print_time(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return "%d Hours %02d Minutes %02.2f Seconds" % (h, m, s)
    elif m > 0:
        return "%2d Minutes %02.2f Seconds" % (m, s)
    else:
        return "%2.2f Seconds" % s


class TensorboardValidationCallback(Callback):
    def __init__(self,
                 infer_model,
                 training_generator,
                 validation_generator,
                 summarization_analysis,
                 tensorboard_callback,
                 num_plot_images=5):
        super().__init__()
        self.training_generator = training_generator
        self.validation_generator = validation_generator
        self.infer_model = infer_model
        self.summarization_analysis = summarization_analysis
        self.tensorboard_callback = tensorboard_callback

        # Now we load in the images to monitor
        train_idxs = np.sort(np.random.choice(training_generator.num_images, num_plot_images, replace=False))
        valid_idxs = np.sort(np.random.choice(validation_generator.num_images, num_plot_images, replace=False))
        train_batch_size = training_generator.batch_size
        valid_batch_size = validation_generator.batch_size

        sess = K.get_session()

        # Collect the training examples we care about
        start_time = time.time()
        data_iterator = self.training_generator.get_iterator()
        next_batch = data_iterator.get_next()
        current_index = 0
        self.train_images = []
        self.train_gt = []
        self.train_filenames = []
        for i in range(len(self.training_generator)):
            batch = sess.run(next_batch)
            images, gt, filenames = batch[0], batch[1], batch[2]
            match_found = True
            while match_found:
                match_found = False
                if current_index < len(train_idxs) and train_idxs[current_index] < (i + 1) * train_batch_size:
                    index_in_batch = train_idxs[current_index] % train_batch_size
                    self.train_images.append(images[index_in_batch, :, :, :])
                    self.train_gt.append(gt[index_in_batch, :, :])
                    self.train_filenames.append(filenames[index_in_batch])
                    current_index += 1
                    match_found = True
        print("Initial Training Pass Time: " + print_time(time.time() - start_time))

        # Collect the validation examples we care about
        start_time = time.time()
        data_iterator = self.validation_generator.get_iterator()
        next_batch = data_iterator.get_next()
        current_index = 0
        self.valid_images = []
        self.valid_gt = []
        self.valid_filenames = []
        for i in range(len(self.validation_generator)):
            batch = sess.run(next_batch)
            images, gt, filenames = batch[0], batch[1], batch[2]
            match_found = True
            while match_found:
                match_found = False
                if current_index < len(valid_idxs) and valid_idxs[current_index] < (i + 1) * valid_batch_size:
                    index_in_batch = valid_idxs[current_index] % valid_batch_size
                    self.valid_images.append(images[index_in_batch, :, :, :])
                    self.valid_gt.append(gt[index_in_batch, :, :])
                    self.valid_filenames.append(filenames[index_in_batch])
                    current_index += 1
                    match_found = True
        print("Initial Validation Pass Time: " + print_time(time.time() - start_time))

    def plot_images_in_tensorboard(self, image, gt, epoch, plot_tag="TB Image Plot"):
        sess = K.get_session()

        # Make a fake batch so we can use the default inference model
        curr_image = np.expand_dims(image, axis=0)
        image_batch = np.repeat(curr_image, self.infer_model.infer_batch_size, axis=0)

        # Execute the model on this image
        infer_results = self.infer_model(image_batch)
        infer_results = infer_results[0, :, :]

        # Because continuous "not a valid bounding box" warnings make me anxious...
        corrected_infer_results = tf.maximum(infer_results, 0.0)
        corrected_infer_results = tf.minimum(corrected_infer_results, 1.0)
        corrected_infer_results = sess.run(corrected_infer_results)

        # Mark up the images with the predicted bounding boxes
        drawn_images = self.markup_images(curr_image[0, :, :, :], corrected_infer_results, gt)
        image_protobuf = self.make_image_protobuf(drawn_images)

        # Push a sample image from this batch to TensorBoard
        summary = tf.Summary(value=[tf.Summary.Value(tag=plot_tag, image=image_protobuf)])
        self.get_writer().add_summary(summary, epoch)
        self.get_writer().flush()

    def get_writer(self):
        return self.tensorboard_callback.writer

    def make_image_protobuf(self, tensor):
        """
        Convert an numpy representation image to Image protobuf.
        Copied from https://github.com/lanpa/tensorboard-pytorch/
        """
        height, width, channel = tensor.shape
        image = Image.fromarray(tensor)
        output = io.BytesIO()
        image.save(output, format='PNG')
        image_string = output.getvalue()
        output.close()
        return tf.Summary.Image(height=height,
                                width=width,
                                colorspace=channel,
                                encoded_image_string=image_string)

    def markup_images(self, image, pred_boxes, gt_boxes, confidence_threshold=0.5):
        # Make this an RGB on the appropriate scale/dtype
        image = np.stack([image[:, :, -1], image[:, :, -1], image[:, :, -1]], axis=-1)
        image_min = np.min(image)
        image_max = np.max(image)
        image = (image - image_min) / (image_max - image_min)
        image = (255 * image).astype(np.uint8)

        h, w = image.shape[0], image.shape[1]

        # Draw the predicted boxes
        for j in range(pred_boxes.shape[0]):
            box = pred_boxes[j, :]

            # Only draw the boxes that have a sufficiently high score
            if box[5] > confidence_threshold:
                pt1 = (int(w * box[1]), int(h * box[0]))
                pt2 = (int(w * box[3]), int(h * box[2]))
                cv2.rectangle(image, pt1, pt2, (0, 255, 0), 2)

        # Draw the ground truth boxes
        for j in range(gt_boxes.shape[0]):
            box = gt_boxes[j, :]
            pt1 = (int(w * box[1]), int(h * box[0]))
            pt2 = (int(w * box[3]), int(h * box[2]))
            cv2.rectangle(image, pt1, pt2, (0, 0, 255), 2)

        return image

    def on_epoch_end(self, epoch, logs={}):
        start_time = time.time()
        # First make plots of our training images
        count = 1
        for img, gt_boxes, filename in zip(self.train_images, self.train_gt, self.train_filenames):
            self.plot_images_in_tensorboard(img, gt_boxes, epoch, "Training Image " + str(count))
            count += 1

        # Now plots the validation images
        count = 1
        for img, gt_boxes, filename in zip(self.valid_images, self.valid_gt, self.valid_filenames):
            self.plot_images_in_tensorboard(img, gt_boxes, epoch, "Validation Image " + str(count))
            count += 1
        print("Image Plotting Time: " + print_time(time.time() - start_time))

        # Need to gather a record of this test
        start_time = time.time()
        inferred_boxes = dict()
        truth_boxes = dict()

        data_iterator = self.validation_generator.get_iterator()
        next_batch = data_iterator.get_next()

        sess = K.get_session()
        for i in range(len(self.validation_generator)):
            valid_batch = sess.run(next_batch)
            test_images, test_gt, test_filenames = valid_batch[0], valid_batch[1], valid_batch[2]

            # Run the model on this validation batch
            infer_results = self.infer_model(test_images)

            # Loop over the batch, adding each record to the detection dictionary
            for j in range(infer_results.shape[0]):
                image_results = infer_results[j, :, :]
                image_gt = test_gt[j, :, :]
                image_filename = test_filenames[j][0]

                # Removed the padded (i.e. non-positive) bounding boxes from the ground truth
                gt_positives = image_gt[:, 4] > 0
                image_gt = image_gt[gt_positives, :]

                # Record the results
                # Iterate over the list of inferred boxes and scores...
                predicted_boxes = image_results[:, :4]
                predicted_scores = image_results[:, 5]
                scored_boxes = []
                for box, score in zip(predicted_boxes, predicted_scores):
                    scored_box = {
                        "box": box,
                        "confidence": score,
                    }
                    scored_boxes.append(scored_box)
                inferred_boxes[image_filename] = list(scored_boxes)
                truth_boxes[image_filename] = list(image_gt[:, :4])

        # Run the analysis/Compute the statistics
        iou_list = np.linspace(0.5, 0.9, 3)

        def sigmoid(x):
            return 1 / (1 + np.exp(-x))

        confidence_list = sigmoid(np.linspace(-100, 100, 100))
        confidence_list = np.concatenate([[0.0], confidence_list, [1.0]], axis=0)
        detection_analysis = self.summarization_analysis(truth_boxes,
                                                         inferred_boxes,
                                                         iou_thresholds=iou_list,
                                                         confidence_thresholds=confidence_list)
        total_df = detection_analysis.compute_statistics()

        # Now produce a summary for each IOU threshold
        for iou_val in iou_list:
            iou_df = total_df.loc[iou_val, :]
            tp = iou_df["true_positives"].tolist()
            num_unique_confidences = len(tp)
            fp = iou_df["false_positives"].tolist()
            tn = num_unique_confidences * [0]
            fn = iou_df["false_negatives"].tolist()
            precision = iou_df["precision"].tolist()
            recall = iou_df["recall"].tolist()

            # print("true_positive_counts = " + str(len(tp)))
            # print("false_positive_counts = " + str(len(fp)))
            # print("true_negative_counts = " + str(len(tn)))
            # print("false_negative_counts = " + str(len(fn)))
            # print("precision = " + str(len(precision)))
            # print("recall = " + str(len(recall)))
            # print("num_thresholds = " + str(len(confidence_list)))
            # print("-----------------------------------------------------------")

            pr_summary = summary_lib.pr_curve_raw_data_pb(
                name='PR Curve (IOU = ' + str(iou_val) + ")",
                true_positive_counts=tp,
                false_positive_counts=fp,
                true_negative_counts=tn,
                false_negative_counts=fn,
                precision=precision,
                recall=recall,
                num_thresholds=num_unique_confidences,
                display_name='PR Curve with IOU = ' + str(iou_val))

            self.get_writer().add_summary(pr_summary, epoch)

        print("PR Curve Generation Time: " + print_time(time.time() - start_time))