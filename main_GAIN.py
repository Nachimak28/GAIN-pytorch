import PIL.Image
import pathlib
import torch
import torchvision
from torch import nn
import torch.nn.functional as F
from torchvision.models import vgg19, wide_resnet101_2, mobilenet_v2
import numpy as np
import matplotlib.pyplot as plt

from dataloaders import data
from utils.image import show_cam_on_image, preprocess_image, deprocess_image, denorm

from models.GAIN import GAIN
from PIL import Image
from tensorboardX import SummaryWriter




def main():
    categories = [
                'aeroplane', 'bicycle', 'bird', 'boat', 'bottle', 'bus', 'car',
                'cat', 'chair', 'cow', 'diningtable', 'dog', 'horse', 'motorbike',
                'person', 'pottedplant', 'sheep', 'sofa', 'train', 'tvmonitor'
            ]

    num_classes = len(categories)
    device = torch.device('cuda:0')
    model = vgg19(pretrained=True).train().to(device)

    #model = mobilenet_v2(pretrained=True).train().to(device)

    #change the last layer for finetuning
    classifier = model.classifier
    num_ftrs = classifier[-1].in_features
    new_classifier = torch.nn.Sequential(*(list(model.classifier.children())[:-1]), nn.Linear(num_ftrs, num_classes).to(device))
    model.classifier = new_classifier
    model.train()
    # target_layer = model.features[-1]

    mean = [0.485, 0.456, 0.406]
    std = [0.229, 0.224, 0.225]

    pl_im = PIL.Image.open('C:/VOC-dataset/VOCdevkit/VOC2012/JPEGImages/2007_000032.jpg')
    np_im = np.array(pl_im)
    plt.imshow(np_im)
    plt.show()

    #input_tensor = preprocess_image(np_im, mean=mean, std=std).to(device)
    #input_tensor = torch.from_numpy(np_im).unsqueeze(0).permute([0,3,1,2]).to(device).float()
    #np_im = input_tensor.squeeze().permute(1,2,0).cpu()


    dataset_path = 'C:/VOC-dataset'
    input_dims = [224, 224]
    batch_size_dict = {'train': 1, 'test': 1}
    rds = data.RawDataset(root_dir=dataset_path,
                          num_workers=0,
                          output_dims=input_dims,
                          batch_size_dict=batch_size_dict)

    num_train_samples = len(rds.datasets['seq_train'])
    print(num_train_samples)

    num_test_samples = len(rds.datasets['seq_test'])
    print(num_test_samples)


    epochs = 10
    loss_fn = torch.nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.00001)
    gain = GAIN(model=model, grad_layer='features', num_classes=20, pretraining_epochs=5, mean=mean, std=std)

    loss_factor = 0.5
    am_factor = 0.5

    epoch_train_single_accuracy = []
    epoch_train_multi_accuracy = []
    epoch_test_single_accuracy = []
    epoch_test_multi_accuracy = []


    viz_path = 'C:/Users/Student1/PycharmProjects/GCAM/exp2_GAIN'
    pathlib.Path(viz_path).mkdir(parents=True, exist_ok=True)

    start_writing_iteration = 5

    for epoch in range(epochs):
        total_train_single_accuracy = 0
        total_train_multi_accuracy = 0
        total_test_single_accuracy = 0
        total_test_multi_accuracy = 0


        train_accuracy = []
        mean_train_accuracy = []
        test_accuracy = []
        mean_test_accuracy = []
        train_epoch_cl_loss = []
        test_epoch_cl_loss = []

        train_epoch_am_loss = []
        test_epoch_am_loss = []

        train_epoch_total_loss = []
        test_epoch_total_loss = []

        train_multi_accuracy = []
        mean_train_multi_accuracy = []
        test_multi_accuracy = []
        mean_test_multi_accuracy = []


        train_path = 'C:/Users/Student1/PycharmProjects/GCAM/exp2_GAIN/train'
        pathlib.Path(train_path).mkdir(parents=True, exist_ok=True)
        epoch_path = train_path+'/epoch_'+str(epoch)
        pathlib.Path(epoch_path).mkdir(parents=True, exist_ok=True)

        model.train(True)
        i = 0
        for sample in rds.datasets['seq_train']:
            input_tensor = preprocess_image(sample['image'].squeeze().numpy(), mean=mean, std=std).to(device)
            label_idx_list = sample['label/idx']
            num_of_labels = len(label_idx_list)
            optimizer.zero_grad()
            labels = torch.Tensor(label_idx_list).to(device).long()

            # logits = model(input_tensor)
            logits_cl, logits_am, heatmap, masked_image = gain(input_tensor, labels)

            indices = torch.Tensor(label_idx_list).long().to(device)
            class_onehot = torch.nn.functional.one_hot(indices, num_classes).sum(dim=0).unsqueeze(0).float()

            cl_loss = loss_fn(logits_cl, class_onehot)

            am_loss = nn.Softmax(dim=1)(logits_am)
            am_loss, am_labels = am_loss.topk(num_of_labels)
            am_loss = am_loss.sum() / am_loss.size(1)

            total_loss = cl_loss * loss_factor
            total_loss += am_loss * am_factor

            if gain.AM_enabled():
                loss = total_loss
            else:
                loss = cl_loss

            loss.backward()
            optimizer.step()

            # Single label evaluation
            y_pred = logits_cl.detach().argmax()
            y_pred = y_pred.view(-1)
            gt, _ = indices.sort(descending=True)
            gt = gt.view(-1)
            acc = (y_pred == gt).sum()
            total_train_single_accuracy += acc.detach().cpu()

            # Multi label evaluation
            _, y_pred_multi = logits_cl.detach().topk(num_of_labels)
            y_pred_multi = y_pred_multi.view(-1)
            acc_multi = (y_pred_multi == gt).sum() / num_of_labels
            total_train_multi_accuracy += acc_multi.detach().cpu()

            if i % 100 == 0:
                print(i)
                print('Classification Loss per image: {:.3f}'.format(cl_loss.detach().item()))
                print('AM Loss per image: {:.3f}'.format(am_loss.detach().item()))
                print('Total Loss per image: {:.3f}'.format(total_loss.detach().item()))
                train_accuracy.append(acc.detach().cpu())
                train_multi_accuracy.append(acc_multi.detach().cpu())

            if i % 200 == 0:
                train_epoch_cl_loss.append(cl_loss.detach().item())
                train_epoch_am_loss.append(am_loss.detach().item())
                train_epoch_total_loss.append(am_loss.detach().item())
                if len(train_accuracy) > start_writing_iteration:
                    acc_mean = (sum(train_accuracy) / len(train_accuracy)).detach().cpu()
                    mean_train_accuracy.append(acc_mean)
                    acc_multi_mean = (sum(train_multi_accuracy) / len(train_multi_accuracy)).detach().cpu()
                    mean_train_multi_accuracy.append(acc_multi_mean)
                    print('Average train single label accuracy: {:.3f}'.format(acc_mean))
                    print('Average train multi label accuracy: {:.3f}'.format(acc_multi_mean))

                _, y_pred = logits_cl.detach().topk(num_of_labels)

                y_pred = y_pred.view(-1)
                gt, _ = y_pred.sort(descending=True)

                predicted_categories = [categories[x] for x in gt]

                labels = [categories[label_idx] for label_idx in label_idx_list]
                cl_loss = loss.detach().item()
                dir_name = str(i)+'_labels_'+'_'.join(labels)+'_predicted_'+'_'.join(predicted_categories) +'_loss_'+str(cl_loss)
                dir_path = epoch_path + '/' + dir_name
                pathlib.Path(dir_path).mkdir(parents=True, exist_ok=True)

                img = sample['image'].squeeze().numpy()
                img_orig = Image.fromarray(img)

                # for comparasion to am score: should be bigger
                y_scores, _ = nn.Softmax(dim=1)(logits_cl.detach()).topk(num_of_labels)
                y_scores = y_scores.sum() / y_scores.size(1)

                img_orig.save(dir_path + '/' + 'orig_'+str(y_scores.detach().item())+'.jpg')


                htm = heatmap.squeeze().cpu().detach().numpy()
                #plt.imshow(htm)
                #plt.show()

                htm = deprocess_image(htm)
                visualization, heatmap = show_cam_on_image(img, htm, True)
                visualization_m = Image.fromarray(visualization)
                visualization_m.save(dir_path+'/'+'vis.jpg')

                masked_image = denorm(masked_image.detach(), mean, std)
                masked_image = (masked_image.squeeze().permute([1, 2, 0]).cpu().detach().numpy() * 255).astype(
                    np.uint8)
                masked_image_m = Image.fromarray(masked_image)

                am_loss = am_loss.detach().item()
                predicted_am_categories = [categories[x] for x in am_labels]
                masked_image_m.save(dir_path + '/' + 'masked_img_'+'_'.join('predicted_am_categories')+'_'+str(am_loss)+'.jpg')

                if len(train_epoch_cl_loss) > 1:
                    #mx = max(epoch_loss)
                    #smooth = [l / mx for l in epoch_loss]
                    x_loss = np.arange(0, i+1, 200)

                    plt.plot(x_loss, train_epoch_cl_loss)
                    plt.savefig(epoch_path+'/epoch_cl_loss.jpg')
                    plt.close()


                    plt.plot(x_loss, train_epoch_am_loss)
                    plt.savefig(epoch_path + '/epoch_am_loss.jpg')
                    plt.close()

                    plt.plot(x_loss, train_epoch_total_loss)
                    plt.savefig(epoch_path + '/epoch_total_loss.jpg')
                    plt.close()

                    #plt.plot(smooth)
                    #plt.savefig(epoch_path + '/smooth.jpg')
                    plt.close()
                    if i % 200 == 0 and i > start_writing_iteration * 100:
                        x_acc = np.arange(600, i + 1, 200)
                        plt.plot(x_acc, mean_train_accuracy)
                        plt.savefig(epoch_path + '/train_accuracy.jpg')
                        plt.close()
                        plt.plot(x_acc, mean_train_multi_accuracy)
                        plt.savefig(epoch_path + '/train_multi_accuracy.jpg')
                        plt.close()
            i+=1

        test_path = 'C:/Users/Student1/PycharmProjects/GCAM/exp2_GAIN/test'
        pathlib.Path(test_path).mkdir(parents=True, exist_ok=True)
        epoch_path = test_path + '/epoch_' + str(epoch)
        pathlib.Path(epoch_path).mkdir(parents=True, exist_ok=True)

        model.train(False)
        i = 0
        for sample in rds.datasets['seq_test']:
            input_tensor = preprocess_image(sample['image'].squeeze().numpy(), mean=mean, std=std).to(device)
            label_idx_list = sample['label/idx']
            num_of_labels = len(label_idx_list)
            optimizer.zero_grad()
            labels = torch.Tensor(label_idx_list).to(device).long()

            # logits = model(input_tensor)
            logits_cl, logits_am, heatmap, masked_image = gain(input_tensor, labels)
            indices = torch.Tensor(label_idx_list).long().to(device)
            class_onehot = torch.nn.functional.one_hot(indices, num_classes).sum(dim=0).unsqueeze(0).float()

            cl_loss = loss_fn(logits_cl, class_onehot)

            total_loss = cl_loss * loss_factor
            am_loss = nn.Softmax(dim=1)(logits_am)
            am_loss, _ = am_loss.topk(num_of_labels)
            am_loss = am_loss.sum() / am_loss.size(1)
            total_loss += am_loss * am_factor

            # Single label evaluation
            y_pred = logits_cl.detach().argmax()
            y_pred = y_pred.view(-1)
            gt, _ = indices.sort(descending=True)
            gt = gt.view(-1)
            acc = (y_pred == gt).sum()
            total_test_single_accuracy += acc.detach().cpu()

            # Multi label evaluation
            _, y_pred_multi = logits_cl.detach().topk(num_of_labels)
            y_pred_multi = y_pred_multi.view(-1)
            acc_multi = (y_pred_multi == gt).sum() / num_of_labels
            total_test_multi_accuracy += acc_multi.detach().cpu()


            if i % 25 == 0:
                print(i)
                print('Classification Loss per image: {:.3f}'.format(cl_loss.detach().item()))
                print('AM Loss per image: {:.3f}'.format(am_loss.detach().item()))
                print('Total Loss per image: {:.3f}'.format(total_loss.detach().item()))
                test_accuracy.append(acc.detach().cpu())
                test_multi_accuracy.append(acc_multi.detach().cpu())

            if i % 50 == 0:
                test_epoch_cl_loss.append(cl_loss.detach().item())
                test_epoch_am_loss.append(am_loss.detach().item())
                test_epoch_total_loss.append(total_loss.detach().item())
                if len(test_accuracy) > start_writing_iteration:
                    acc_mean = (sum(test_accuracy) / len(test_accuracy)).detach().cpu()
                    mean_test_accuracy.append(acc_mean)
                    acc_multi_mean = (sum(test_multi_accuracy) / len(test_multi_accuracy)).detach().cpu()
                    mean_test_multi_accuracy.append(acc_multi_mean)
                    print('Average test single label accuracy: {:.3f}'.format(acc_mean))
                    print('Average test multi label accuracy: {:.3f}'.format(acc_multi_mean))

                _, y_pred = logits_cl.detach().topk(num_of_labels)
                y_pred = y_pred.view(-1)
                gt, _ = y_pred.sort(descending=True)

                predicted_categories = [categories[x] for x in gt]

                labels = [categories[label_idx] for label_idx in label_idx_list]
                cl_loss = cl_loss.detach().item()
                dir_name = str(i) + '_labels_' + '_'.join(labels) + '_predicted_' + '_'.join(
                    predicted_categories) + '_loss_' + str(cl_loss)
                dir_path = epoch_path + '/' + dir_name
                pathlib.Path(dir_path).mkdir(parents=True, exist_ok=True)

                img = sample['image'].squeeze().numpy()
                img_orig = Image.fromarray(img)

                # for comparasion to am score: should be bigger
                y_scores, _ = nn.Softmax(dim=1)(logits_cl.detach()).topk(num_of_labels)
                y_scores = y_scores.sum() / y_scores.size(1)

                img_orig.save(dir_path + '/' + 'orig_'+str(y_scores.detach().item())+'.jpg')

                htm = heatmap.squeeze().cpu().detach().numpy()
                # plt.imshow(htm)
                # plt.show()

                htm = deprocess_image(htm)
                visualization, heatmap = show_cam_on_image(img, htm, True)
                visualization_m = Image.fromarray(visualization)
                visualization_m.save(dir_path+'/'+'vis.jpg')
                masked_image = denorm(masked_image.detach(), mean, std)
                masked_image = (masked_image.squeeze().permute([1, 2, 0]).cpu().detach().numpy() * 255).astype(
                    np.uint8)
                masked_image_m = Image.fromarray(masked_image)
                am_loss = am_loss.detach().item()
                masked_image_m.save(dir_path + '/' + 'masked_img_'+str(am_loss)+'.jpg')
                # plt.imshow(visualization)
                # plt.show()
                # plt.imshow(heatmap)
                # plt.show()
                # print()

                if len(test_epoch_cl_loss) > 1:
                    # mx = max(epoch_loss)
                    # smooth = [l / mx for l in epoch_loss]
                    x_loss = np.arange(0, i + 1, 50)
                    plt.plot(x_loss, test_epoch_cl_loss)
                    plt.savefig(epoch_path + '/epoch_cl_loss.jpg')
                    plt.close()

                    plt.plot(x_loss, test_epoch_am_loss)
                    plt.savefig(epoch_path + '/epoch_am_loss.jpg')
                    plt.close()

                    plt.plot(x_loss, test_epoch_total_loss)
                    plt.savefig(epoch_path + '/epoch_total_loss.jpg')
                    plt.close()
                    if i % 50 == 0 and i > start_writing_iteration * 25:
                        x_acc = np.arange(150, i + 1, 50)
                        plt.plot(x_acc, mean_test_accuracy)
                        plt.savefig(epoch_path + '/test_accuracy.jpg')
                        plt.close()
                        plt.plot(x_acc, mean_test_multi_accuracy)
                        plt.savefig(epoch_path + '/test_multi_accuracy.jpg')
                        plt.close()
            i += 1

        print("finished epoch number:")
        print(epoch)

        gain.increase_epoch_count()

        chkpt_path = 'C:/Users/Student1/PycharmProjects/GCAM/checkpoints/'+str(epoch)
        pathlib.Path(train_path).mkdir(parents=True, exist_ok=True)

        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
        }, chkpt_path)

        epoch_train_single_accuracy.append(total_train_single_accuracy / num_train_samples)
        print('Average epoch single train accuracy: {:.3f}'.format(total_train_single_accuracy / num_train_samples))

        epoch_train_multi_accuracy.append(total_train_multi_accuracy / num_train_samples)
        print('Average epoch multi train accuracy: {:.3f}'.format(total_train_multi_accuracy / num_train_samples))

        epoch_test_single_accuracy.append(total_test_single_accuracy / num_test_samples)
        print('Average epoch single test accuracy: {:.3f}'.format(total_test_single_accuracy / num_test_samples))

        epoch_test_multi_accuracy.append(total_test_multi_accuracy / num_test_samples)
        print('Average epoch multi test accuracy: {:.3f}'.format(total_test_multi_accuracy / num_test_samples))

        plt.plot(list(range(len(epoch_train_single_accuracy))), epoch_train_single_accuracy)
        plt.savefig(viz_path + '/epoch_train_single_accuracy.jpg')
        plt.close()
        plt.plot(list(range(len(epoch_train_multi_accuracy))), epoch_train_multi_accuracy)
        plt.savefig(viz_path + '/epoch_train_multi_accuracy.jpg')
        plt.close()
        plt.plot(list(range(len(epoch_test_single_accuracy))), epoch_test_single_accuracy)
        plt.savefig(viz_path + '/epoch_test_single_accuracy.jpg')
        plt.close()
        plt.plot(list(range(len(epoch_test_multi_accuracy))), epoch_test_multi_accuracy)
        plt.savefig(viz_path + '/epoch_test_multi_accuracy.jpg')
        plt.close()




if __name__ == '__main__':
    main()
    print()